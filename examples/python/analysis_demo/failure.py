"""Deterministic failed continuation, using the same workspace-job outcome boundary."""

from uuid import uuid4

from codingagent_demo.job import run_workspace_job


def run_failed_followup(client, image, output, files, step_id):
    result_dir = "/opt/coding-results/" + uuid4().hex
    script = (
        "import json, tarfile\nfrom pathlib import Path\n"
        f"output = Path({result_dir!r}); output.mkdir(parents=True)\n"
        f"child = Path('/workspace/steps/{step_id}'); child.mkdir(parents=True)\n"
        "(child / 'failure.txt').write_text('Controlled child change before failure')\n"
        "with tarfile.open(output / 'workspace.tar.gz', 'w:gz') as archive:\n"
        "    archive.add('/workspace', arcname='workspace')\n"
        "(output / 'result.json').write_text(json.dumps({'status': 'failed', 'answer': '', "
        "'error': 'Controlled follow-up failure after writing child workspace'}))\n"
        "(output / 'stderr.log').write_text('Controlled failure; no inference was requested')\n"
        "print('Controlled failure; child checkpoint and diagnostics retained')\n"
    )
    uploads = {"/workspace/" + path.name: client.upload(path.read_bytes()) for path in files}
    uploads["/opt/analysis-failure.py"] = client.upload(script.encode())
    return run_workspace_job(
        client,
        image,
        output=output,
        timeout=60,
        files=uploads,
        script="/opt/analysis-failure.py",
        result_dir=result_dir,
        metadata={"controlled_failure": True},
    )
