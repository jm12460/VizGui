"""Run the phone's polling renderer with a minimal DOM and fake API data."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("state", ["starting", "failed", "playing", "unreachable"])
def test_phone_renders_playback_request_result_as_text(state):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not available for the phone renderer check")
    html = (Path(__file__).resolve().parents[1] / "vizgui/remote.html").read_text()
    formatting = html[html.index("function fmt(s)"):html.index("function syncRecovery(d)")]
    polling = html[html.index("async function poll(){"):html.index("function toggleAN()")]
    status = {"ok": True, "targets": [], "sources": [], "target_index": 0,
              "now": {"title": "Episode", "pos": 60, "dur": 600, "state": "PLAYING"},
              "playback_request": {"id": "test-request", "state": state,
                                   "title": "New episode", "error": "<b>device failed</b>"}}
    if state == "unreachable":
        status["receiver_unreachable"] = True
    script = """
const nodes={};
function $(id){return nodes[id]||(nodes[id]={style:{},textContent:'',value:'',
  classList:{toggle(){}},replaceChildren(){},setAttribute(){}});}
let lastTargets=null,lastSources=null,anState=false,CHAPTERS=[],AUDIO={},SUBS={};
let pollSeq=0;
function syncRecovery(){}
const I_PAUSE='',I_PLAY='';
function opt(){return {};}
""" + "const api=async()=>(" + json.dumps(status) + ");\n" + formatting + polling + """
poll().then(()=>process.stdout.write(JSON.stringify({
  title:$('np').textContent,time:$('time').textContent,width:$('prog').style.width
}))).catch(error=>{process.stderr.write(String(error));process.exit(1);});
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    if state == "starting":
        assert rendered["title"] == "Starting: New episode…"
        assert rendered["time"] == "Waiting for playback confirmation"
    elif state == "failed":
        assert rendered["title"] == "Playback failed: New episode"
        assert rendered["time"] == "<b>device failed</b>"
    elif state == "unreachable":
        assert rendered["time"] == "Cast receiver unreachable · last known position 1:00"
    else:
        assert rendered["title"] == "Episode"
        assert rendered["time"] == "1:00 / 10:00"
