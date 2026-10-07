import json
import math
import random
import sqlite3
from pathlib import Path
import pytest
from aemeath_pet.model import load_settings, save_settings, gaze_direction, GazeActivity, AnimationDirector, IdleScheduler, popup_position, CompletionReminder
from aemeath_pet.navigation import thread_url
from aemeath_pet.input_state import covers_monitor
from aemeath_pet.codex import RolloutReader, TaskMonitor, normalize_limits

def event(kind, turn="turn-1", **kwargs):
    return json.dumps({"type":"event_msg", "payload":{"type":kind, "turn_id":turn, **kwargs}})+"\n"

def test_v1_preferences_migrate_without_care(tmp_path):
    path=tmp_path/"state.json"
    path.write_text(json.dumps({"version":1,"care":{"xp":999,"fullness":0},"settings":{"gaze":False,"scale":1.5,"position":[100,200],"resting":True}}))
    settings=load_settings(path)
    assert settings["gaze"] is False and settings["scale"]==1.5
    assert settings["position"]==[100,200] and "resting" not in settings
    save_settings(path,settings)
    raw=json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"]==2 and "care" not in raw

def test_animation_priority_and_recovery():
    director=AnimationDirector()
    assert director.start("sleep",10,0,10)
    assert director.start("wave",2,1,50)
    assert director.start("celebrate",4,2,100)
    assert not director.start("eat",4,3,10)
    assert not director.start("wave",2,3,50)
    assert director.current(6) is None
    assert director.start("wave",2,6,50)

def test_bad_save_recovers(tmp_path):
    path = tmp_path/"state.json"
    path.write_text('invalid json')
    assert load_settings(path)["auto_idle"] is True

def test_pending_completion_survives_restart_until_matching_review(tmp_path):
    path=tmp_path/'pending.json';queue=CompletionReminder(path)
    assert queue.add('thread-a','turn-a','完成的任务')
    assert not queue.add('thread-a','turn-a','重复事件')
    queue.add('thread-b','turn-b','另一个任务')
    restored=CompletionReminder(path)
    assert len(restored.items)==2 and restored.latest['thread_id']=='thread-b'
    restored.reviewed('thread-b','old-turn')
    assert len(restored.items)==2
    restored.reviewed('thread-b','turn-b')
    assert restored.latest['thread_id']=='thread-a'
    restored.add('thread-a','new-turn','最新完成结果')
    restored.reviewed('thread-a','turn-a')
    assert restored.latest['turn_id']=='new-turn'  # A delayed jump cannot clear a newer result.
    restored.reviewed('thread-a','new-turn')
    assert not CompletionReminder(path).items

def test_volume_is_persisted_clamped_and_uri_cannot_contain_prompt(tmp_path):
    path=tmp_path/'state.json';path.write_text(json.dumps({'settings':{'sound_volume':200}}))
    assert load_settings(path)['sound_volume']==100
    path.write_text(json.dumps({'settings':{'sound_volume':-5}}))
    assert load_settings(path)['sound_volume']==0
    path.write_text(json.dumps({'settings':{'sound_volume':float('nan')}}))
    assert load_settings(path)['sound_volume']==85
    assert thread_url('00000000-0000-4000-8000-000000000001')=='codex://threads/00000000-0000-4000-8000-000000000001'
    with pytest.raises(ValueError):thread_url('new?prompt=do-something')

def test_idle_variety_and_no_action_during_hover_or_work():
    scheduler=IdleScheduler(0,22,45,random.Random(123))
    assert scheduler.choose(20) is None
    assert scheduler.choose(100,busy=True) is None
    assert scheduler.choose(100,hovered=True) is None
    chosen=[]
    for _ in range(50):
        chosen.append(scheduler.choose(scheduler.next_at))
    assert set(chosen)==set(scheduler.actions)
    assert all(a!=b for a,b in zip(chosen,chosen[1:]))
    scheduler.touch(500)
    assert 522<=scheduler.next_at<=545

def test_popup_stays_on_screen_edges_and_secondary_monitor():
    for anchor,screen in (((1850,900,250,270),(0,0,1920,1080)),((0,0,250,270),(0,0,1920,1080)),((-1850,700,250,270),(-1920,0,1920,1080))):
        x,y=popup_position(anchor,(340,302),screen)
        sx,sy,sw,sh=screen
        assert sx<=x<=sx+sw-340 and sy<=y<=sy+sh-302

def test_global_gaze_geometry():
    assert gaze_direction(0,-500) == 0
    assert gaze_direction(500,0) == 4
    assert gaze_direction(0,500) == 8
    assert gaze_direction(-500,0) == 12
    assert gaze_direction(0,0) is None
    assert gaze_direction(350,-350) == 2
    assert gaze_direction(350,-350,previous=2) == 2
    for i in range(16):
        a=math.radians(i*22.5)
        assert gaze_direction(math.sin(a)*500,-math.cos(a)*500)==i

def test_gaze_requires_sustained_motion_and_does_not_lock_after_stop():
    gate=GazeActivity()
    assert not gate.sample(0,(0,0))
    assert not gate.sample(.1,(30,0))  # A single mouse bump is insufficient.
    assert not gate.sample(.5,(30,0))
    assert not gate.sample(.6,(40,0))
    assert not gate.sample(.7,(50,0))
    assert gate.sample(.8,(60,0))
    assert gate.sample(1,(60,0))  # Small grace avoids flicker between samples.
    assert not gate.sample(1.2,(60,0))
    assert gate.reason=="stationary"
    for i in range(20):assert not gate.sample(2+i*.1,(60+(i%2),0))  # One-pixel jitter.

@pytest.mark.parametrize('block,reason',[
    ({'cursor_visible':False},'cursor_hidden'),
    ({'fullscreen':True},'fullscreen'),
    ({'last_key_at':.28},'typing'),
])
def test_gaze_suppression_is_immediate_and_requires_fresh_motion(block,reason):
    gate=GazeActivity();gate.sample(0,(0,0));gate.sample(.1,(10,0));gate.sample(.2,(20,0))
    assert gate.sample(.3,(30,0))
    assert not gate.sample(.31,(40,0),**block) and gate.reason==reason
    assert not gate.sample(2,(40,0))  # Removing the block cannot turn a stale point into gaze.
    assert not gate.sample(2.1,(50,0));assert not gate.sample(2.2,(60,0))
    assert gate.sample(2.3,(70,0))

def test_typing_overrides_continuous_mouse_motion_until_quiet():
    gate=GazeActivity()
    for i in range(12):assert not gate.sample(i*.1,(i*10,10),last_key_at=0)
    assert not gate.sample(1.3,(150,10),last_key_at=0)
    assert not gate.sample(1.4,(160,10),last_key_at=0)
    assert gate.sample(1.5,(170,10),last_key_at=0)
    assert not gate.sample(1.6,(180,10),last_key_at=1.6)

def test_fullscreen_geometry_distinguishes_maximized_and_other_monitors():
    assert covers_monitor((0,0,1920,1080),(0,0,1920,1080))
    assert covers_monitor((-1920,0,0,1080),(-1920,0,0,1080))
    assert covers_monitor((-5,-5,1925,1085),(0,0,1920,1080))
    assert not covers_monitor((0,32,1920,1040),(0,0,1920,1080))
    assert not covers_monitor((10,10,900,700),(0,0,1920,1080))
    assert not covers_monitor((0,0,0,0),(0,0,1920,1080))

def test_quota_prefers_current_bucket_and_missing_is_not_zero():
    normalized=normalize_limits({"rateLimits":{"primary":{"usedPercent":99}},"rateLimitsByLimitId":{"codex":{"primary":{"usedPercent":18,"windowDurationMins":300,"resetsAt":123}}}})
    assert normalized["windows"][0]["remaining"] == 82
    assert len(normalized["windows"]) == 1
    with pytest.raises(RuntimeError):
        normalize_limits({"rateLimits":{"primary":{"usedPercent":None}}})
    with pytest.raises(RuntimeError):
        normalize_limits({"rateLimits":{"primary":{"usedPercent":float('nan')}}})

def test_lifecycle_never_replays_history_and_handles_partial_lines(tmp_path):
    path=tmp_path/"rollout.jsonl"
    path.write_text(event("task_started","old")+event("task_complete","old"),encoding="utf-8")
    reader=RolloutReader(path)
    assert reader.poll() == []
    assert reader.active is None
    with path.open('a',encoding='utf-8') as f:
        f.write(event("task_started"))
        f.write(event("task_complete")[:-3])
    assert reader.poll() == [("started","turn-1")]
    assert reader.active == "turn-1"
    assert reader.poll() == []
    with path.open('a',encoding='utf-8') as f:
        f.write(event("task_complete")[-3:])
        f.write(event("task_complete"))
    assert reader.poll() == [("completed","turn-1")]
    assert reader.active is None

def test_long_completion_and_cancel_are_explicit(tmp_path):
    path=tmp_path/"rollout.jsonl"
    path.write_text('',encoding='utf-8')
    r=RolloutReader(path); r.poll()
    path.write_text(event("task_started")+event("task_complete",last_agent_message="x"*80000)+event("task_started","next")+event("turn_aborted","next"),encoding='utf-8')
    assert r.poll()==[("started","turn-1"),("completed","turn-1"),("started","next"),("aborted","next")]
    assert r.active is None

def test_parallel_tasks_and_closed_app(tmp_path):
    with sqlite3.connect(tmp_path/"state_5.sqlite") as db:
        db.execute("create table threads(id text,title text,rollout_path text,archived int,source text,agent_path text,updated_at int)")
        for i in (1,2):
            path=tmp_path/f"r{i}.jsonl"; path.write_text('',encoding='utf-8')
            db.execute("insert into threads values(?,?,?,0,'vscode',NULL,?)",(str(i),f"task {i}",str(path),i))
    opened=[True]
    monitor=TaskMonitor(tmp_path,is_open=lambda:opened[0])
    assert monitor.poll()==([],[])
    for i in (1,2):
        (tmp_path/f"r{i}.jsonl").write_text(event("task_started",str(i)),encoding='utf-8')
    events,active=monitor.poll()
    assert len(events)==len(active)==2
    with (tmp_path/"r1.jsonl").open('a',encoding='utf-8') as f:
        f.write(event("task_complete","1"))
    events,active=monitor.poll()
    assert [e.kind for e in events]==["completed"]
    assert active==[("2","task 2")]
    opened[0]=False
    assert monitor.poll()==([],[])
    assert monitor.status=="Codex 未打开"

def item(kind, **kwargs):
    return json.dumps({"type":"response_item","payload":{"type":kind,**kwargs}})+"\n"

def test_activity_tracks_parallel_calls_and_recovers_after_output(tmp_path):
    path=tmp_path/"activity.jsonl";path.write_text('',encoding='utf-8')
    reader=RolloutReader(path);reader.poll()
    def append(text):
        with path.open('a',encoding='utf-8') as f:f.write(text)
        reader.poll()
    append(event("task_started"))
    assert reader.phase=="thinking"
    append(item("custom_tool_call",call_id="a",name="functions.exec",input="sensitive")+item("function_call",call_id="b"))
    assert reader.phase=="working" and reader.pending_calls=={"a","b"}
    append(item("reasoning",summary=["private"]))
    assert reader.phase=="working"  # A parallel tool still runs.
    append(item("custom_tool_call_output",call_id="a",output="private"))
    assert reader.phase=="working"
    append(item("function_call_output",call_id="b"))
    assert reader.phase=="thinking" and not reader.pending_calls
    append(event("task_complete"))
    assert reader.phase is None and not reader.pending_calls
    append(item("reasoning"))
    assert reader.phase is None  # Historical/late items cannot resurrect a task.
    assert not hasattr(reader,"content") and not hasattr(reader,"summary")

def test_activity_bootstrap_reconstructs_pending_call_and_partial_output(tmp_path):
    path=tmp_path/"activity.jsonl"
    output=item("custom_tool_call_output",call_id="a")
    path.write_text(event("task_started")+item("custom_tool_call",call_id="a")+output[:-2],encoding='utf-8')
    reader=RolloutReader(path)
    assert reader.poll()==[] and reader.phase=="working"
    with path.open('a',encoding='utf-8') as f:f.write(output[-2:])
    assert reader.poll()==[] and reader.phase=="thinking"
    path.write_text(event("task_started","new"),encoding='utf-8')
    reader.poll()
    assert reader.active=="new" and reader.phase=="thinking" and not reader.pending_calls

def test_monitor_any_tool_wins_across_two_tasks(tmp_path):
    with sqlite3.connect(tmp_path/"state_5.sqlite") as db:
        db.execute("create table threads(id text,title text,rollout_path text,archived int,source text,agent_path text,updated_at int)")
        for i in (1,2):
            path=tmp_path/f"r{i}.jsonl";path.write_text(event("task_started",str(i)),encoding='utf-8')
            db.execute("insert into threads values(?,?,?,0,'vscode',NULL,?)",(str(i),f"task {i}",str(path),i))
    monitor=TaskMonitor(tmp_path,is_open=lambda:True);monitor.poll()
    assert monitor.phase=="thinking"
    with (tmp_path/'r1.jsonl').open('a',encoding='utf-8') as f:f.write(item("function_call",call_id="tool"))
    monitor.poll();assert monitor.phase=="working"
    with (tmp_path/'r1.jsonl').open('a',encoding='utf-8') as f:f.write(event("task_complete","1"))
    monitor.poll();assert monitor.phase=="thinking" and monitor.phases=={"2":"thinking"}
