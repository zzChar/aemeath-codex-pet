import json
from aemeath_pet.reports import ReportCollector,assistant_text,excerpt
from aemeath_pet.codex import RolloutReader
from aemeath_pet.model import CompletionReminder,load_settings

def test_only_visible_assistant_messages_become_reports():
    c=ReportCollector();c.start('turn')
    c.observe({'type':'Reasoning','summary_text':'hidden','raw_content':'private'})
    c.observe({'type':'message','role':'user','content':[{'type':'text','text':'private prompt'}]})
    assert c.events==[] and not c.answer
    c.observe({'type':'AgentMessage','phase':'commentary','content':[{'type':'Text','text':'正在检查设置窗口。'}]})
    assert c.events[0].kind=='progress'
    c.observe({'type':'message','role':'assistant','channel':'commentary','content':[{'type':'output_text','text':'正在检查设置窗口。'}]})
    assert len(c.events)==1
    c.observe({'type':'AgentMessage','phase':'final_answer','content':[{'type':'Text','text':'已修复设置，测试通过。'}]})
    assert c.complete().text=='已修复设置，测试通过。'

def test_excerpt_is_bounded_and_preserves_what_was_actually_said():
    text='**修复完成**。\n[源码](https://example.com)\n```python\nsecret = 1\n```\n<oai-mem-citation>internal</oai-mem-citation>'
    assert excerpt(text)=='修复完成。\n源码\n[代码片段]'
    assert len(excerpt('好'*10000,420))==421
    assert '可能失败' in excerpt('测试可能失败，尚未确认。')

def test_tool_calls_outputs_and_questions_are_not_work_reports():
    c=ReportCollector();c.start('turn')
    for item in (
        {'type':'function_call','name':'request_user_input','arguments':'{"questions":[]}'},
        {'type':'function_call','name':'exec_command','arguments':'{"sandbox_permissions":"require_escalated"}'},
        {'type':'function_call_output','output':'private tool output'},
        {'type':'message','role':'user','content':[{'type':'text','text':'user reply'}]},
    ):c.observe(item)
    assert c.events==[] and c.answer==''

def test_incremental_reports_skip_history_and_wait_for_complete_json_lines(tmp_path):
    p=tmp_path/'r.jsonl'
    def event(kind,**kw):return json.dumps({'type':'event_msg','payload':{'type':kind,'turn_id':'turn',**kw}})+'\n'
    def message(channel,text):return json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant','channel':channel,'content':[{'type':'output_text','text':text}]}})+'\n'
    p.write_text(event('task_started')+message('commentary','旧消息'),encoding='utf-8')
    r=RolloutReader(p);assert r.poll()==[] and r.reports==[]
    line=message('commentary','新进展')
    with p.open('a',encoding='utf-8') as f:f.write(line[:-1])
    r.poll();assert r.reports==[]
    with p.open('a',encoding='utf-8') as f:f.write(line[-1:]+message('final','真实结果')+event('task_complete'))
    assert r.poll()==[('completed','turn')]
    assert r.completion_summaries['turn']=='真实结果'
    assert r.reports[0].text=='新进展'

def test_old_pending_items_migrate_and_summary_survives_restart(tmp_path):
    p=tmp_path/'pending.json';q=CompletionReminder(p)
    q.add('one','turn','旧格式')
    q.add('two','turn','新格式',summary='已修复，并验证通过。')
    q=CompletionReminder(p)
    assert 'summary' not in q.items[0]
    assert q.latest['summary']=='已修复，并验证通过。'
    settings=tmp_path/'state.json'
    settings.write_text(json.dumps({'settings':{'quick_reply':True,'sound_volume':82,'progress_reports':False}}))
    migrated=load_settings(settings)
    assert 'quick_reply' not in migrated and migrated['sound_volume']==82 and migrated['progress_reports'] is False
