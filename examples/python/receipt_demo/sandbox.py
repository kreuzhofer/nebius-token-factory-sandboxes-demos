"""Receipt-specific packaging, dependency bootstrap, and worker runtime settings."""

from pathlib import Path

BOOTSTRAP = """import os, subprocess, sys, time
os.environ['RECEIPT_DEADLINE'] = str(time.monotonic() + int(os.environ.get('RECEIPT_JOB_TIMEOUT', '1800')) - 60)
try:
    install = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
         '--no-cache-dir', '-r', '/app/receipt_demo/requirements.txt'],
        env={'PATH': os.defpath}, capture_output=True, timeout=240)
    if install.returncode:
        print('Dependency installation failed; check the pinned requirements and package access.', flush=True)
        sys.exit(1)
    from receipt_demo.execution import main
    main()
except Exception as exc:
    print('Agent bootstrap failed: ' + type(exc).__name__, flush=True)
    sys.exit(1)
"""


def upload_code(client, root):
    """Upload the same runnable Python files locally and from a coordinator sandbox."""
    root = Path(root)
    paths = [
        root / name
        for name in (
            "configuration.py",
            "http_transport.py",
            "nebius_sandbox.py",
            "sandbox_events.py",
            "requirements.txt",
        )
    ]
    paths.extend(
        path
        for path in sorted((root / "receipt_demo").iterdir())
        if path.name != "__main__.py" and (path.suffix == ".py" or path.name == "requirements.txt")
    )
    return {
        "/app/" + path.relative_to(root).as_posix(): client.upload(path.read_bytes())
        for path in paths
    }


def submit_worker(client, image, files, env, timeout):
    return client.submit(
        image,
        command="/usr/local/bin/python3",
        args=["-u", "-"],
        stdin=BOOTSTRAP,
        cwd="/app",
        files=files,
        env=dict(env, RECEIPT_JOB_TIMEOUT=str(timeout)),
        timeout=timeout,
        disposable=False,
        networking=True,
        max_layer_bytes=1073741824,
        output_limit=1048576,
    )
