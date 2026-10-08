"""Bounded excerpts of public assistant messages; never summarize reasoning."""
import re
from dataclasses import dataclass

def excerpt(text, limit=420):
    if not isinstance(text,str):return ''
    text=re.sub(r'<oai-mem-citation>.*?</oai-mem-citation>','',text,flags=re.S)
    text=re.sub(r'```.*?```','[代码片段]',text,flags=re.S)
    text=re.sub(r'!\[[^\]]*\]\([^)]*\)','[图片]',text)
    text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',text)
    text=re.sub(r'^\s{0,3}#{1,6}\s*','',text,flags=re.M)
    text=text.replace('**','').replace('`','')
    text='\n'.join(line.strip() for line in text.splitlines() if line.strip())
    text=''.join(c for c in text if c=='\n' or ord(c)>=32)
    return text[:limit]+('…' if len(text)>limit else '')

@dataclass
class WorkReport:
    kind: str
    text: str
    turn_id: str

def assistant_text(item):
    if item.get('type') in ('agentMessage','AgentMessage','agent_message'):
        return item.get('text') or item.get('message') or '\n'.join(part.get('text','') for part in item.get('content',[]) if isinstance(part,dict) and part.get('type') in ('Text','text','output_text'))
    if item.get('type')=='message' and item.get('role')=='assistant':
        return '\n'.join(part.get('text','') for part in item.get('content',[]) if isinstance(part,dict) and part.get('type') in ('output_text','text'))
    return ''

class ReportCollector:
    def __init__(self):self.start('')
    def start(self,turn):
        self.turn=turn;self.answer='';self.events=[];self.seen=set()
    def observe(self,item):
        if not self.turn or not isinstance(item,dict):return
        kind=item.get('type')
        if kind in ('reasoning','Reasoning'):return
        text=assistant_text(item)
        if text:
            compact=excerpt(text,600)
            if not compact:return
            phase=item.get('channel') or item.get('phase')
            if phase in ('final','final_answer'):self.answer=compact
            elif phase=='commentary':
                key=('progress',compact)
                if key not in self.seen:
                    self.seen.add(key);self.events.append(WorkReport('progress',compact,self.turn))
            elif kind in ('agentMessage','AgentMessage','agent_message'):
                self.answer=compact
    def complete(self,text=''):
        result=excerpt(text,600) or self.answer or '任务已结束，当前记录没有提供可显示的最终回复。请前往 Codex 查看。'
        return WorkReport('completed',result,self.turn)
