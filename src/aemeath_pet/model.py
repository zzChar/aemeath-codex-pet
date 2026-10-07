"""Companion preferences, interruptible animations, and quiet idle scheduling."""
import json
import math
import random
from pathlib import Path

DEFAULT_SETTINGS = {
    "gaze": True, "gaze_radius": 240, "fixed_direction": 8,
    "auto_idle": True, "hover_quota": True, "task_notifications": True,
    "sound": True, "sound_volume": 85, "scale": 1.3, "position": None, "codex_path": "",
    "idle_min": 22, "idle_max": 45,
    "task_activity": True,
}

def load_settings(path):
    settings = DEFAULT_SETTINGS.copy()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8")).get("settings", {})
        for key in ("gaze", "auto_idle", "hover_quota", "task_notifications", "sound", "task_activity"):
            if isinstance(raw.get(key), bool):
                settings[key] = raw[key]
        for key, low, high in (("sound_volume",0,100),("scale", .8, 2), ("gaze_radius", 80, 600), ("idle_min", 10, 120), ("idle_max", 15, 180)):
            value = raw.get(key, settings[key])
            if isinstance(value, (int,float)) and math.isfinite(value):
                settings[key] = max(low,min(high,value))
        value = raw.get("fixed_direction")
        if isinstance(value,int):
            settings["fixed_direction"] = value % 16
        pos = raw.get("position")
        if isinstance(pos,list) and len(pos)==2 and all(isinstance(x,int) for x in pos):
            settings["position"] = pos
        if isinstance(raw.get("codex_path"),str):
            settings["codex_path"] = raw["codex_path"]
        settings["idle_max"] = max(settings["idle_min"]+1,settings["idle_max"])
    except (OSError,ValueError,TypeError,AttributeError):
        pass
    return settings

def save_settings(path, settings):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps({"version":2,"settings":settings},ensure_ascii=False,indent=2),encoding="utf-8")
    pending.replace(path)

class CompletionReminder:
    """Persist only completed thread/turn IDs and titles until reviewed."""
    def __init__(self, path):
        self.path=Path(path);self.items=[]
        try:
            raw=json.loads(self.path.read_text(encoding='utf-8'))
            for item in raw if isinstance(raw,list) else []:
                if isinstance(item,dict) and all(isinstance(item.get(k),str) and item[k] for k in ('thread_id','turn_id','title')):
                    self.add(item['thread_id'],item['turn_id'],item['title'],save=False)
        except (OSError,ValueError,TypeError):pass

    def add(self,thread_id,turn_id,title,save=True):
        if any(i['thread_id']==thread_id and i['turn_id']==turn_id for i in self.items):return False
        # Opening the latest result in a thread also covers its older results.
        self.items=[i for i in self.items if i['thread_id']!=thread_id]
        self.items.append({'thread_id':thread_id,'turn_id':turn_id,'title':title or 'Codex 任务'})
        if save:self.save()
        return True

    @property
    def latest(self):return self.items[-1] if self.items else None

    def reviewed(self,thread_id,turn_id):
        self.items=[i for i in self.items if (i['thread_id'],i['turn_id'])!=(thread_id,turn_id)]
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        pending=self.path.with_suffix('.tmp')
        pending.write_text(json.dumps(self.items,ensure_ascii=False,indent=2),encoding='utf-8')
        pending.replace(self.path)

def gaze_direction(dx,dy,radius=240,previous=None):
    distance=math.hypot(dx,dy)
    if distance<26:
        return None
    angle=math.degrees(math.atan2(dx,-dy))%360
    candidate=int((angle+11.25)/22.5)%16
    if previous is not None:
        delta=abs((angle-previous*22.5+180)%360-180)
        if delta<14.5:
            return previous
    if distance<radius*.35:
        return 8 if dy>=0 else 0
    return candidate

class GazeActivity:
    """Enable gaze only after intentional sustained motion, never stale position."""
    def __init__(self, start_delay=.18, stop_delay=.35, typing_delay=1.2):
        self.start_delay,self.stop_delay,self.typing_delay=start_delay,stop_delay,typing_delay
        self.previous=None;self.motion_since=None;self.last_motion=float('-inf')
        self.active=False;self.reason="stationary"

    def sample(self, now, position, cursor_visible=True, fullscreen=False, last_key_at=float('-inf')):
        moved=self.previous is not None and math.hypot(position[0]-self.previous[0],position[1]-self.previous[1])>=2
        self.previous=position
        blocked="cursor_hidden" if not cursor_visible else "fullscreen" if fullscreen else "typing" if now-last_key_at<self.typing_delay else None
        if blocked:
            self.active=False;self.motion_since=None;self.last_motion=float('-inf');self.reason=blocked
            return False
        if moved:
            if now-self.last_motion>self.stop_delay:self.motion_since=now
            self.last_motion=now
            if self.motion_since is not None and now-self.motion_since>=self.start_delay:self.active=True
        elif now-self.last_motion>self.stop_delay:
            self.active=False;self.motion_since=None
        self.reason="moving" if self.active else "stationary"
        return self.active

class AnimationDirector:
    def __init__(self):
        self.kind=None
        self.started=0
        self.until=0
        self.priority=0

    def current(self,now):
        return self.kind if now<self.until else None

    def start(self,kind,duration,now,priority=10):
        if self.current(now) and self.priority>priority:
            return False
        self.kind,self.started,self.until,self.priority=kind,now,now+duration,priority
        return True

    def frame(self,now,count,period=.14):
        return int(max(0,now-self.started)/period)%count

class IdleScheduler:
    actions=("wave","eat","sleep","jump","stretch","lookaround")
    durations={"wave":2.8,"eat":4.2,"sleep":10.5,"jump":1.5,"stretch":3.6,"lookaround":3.5}

    def __init__(self,now,minimum=22,maximum=45,rng=None):
        self.rng=rng or random.Random()
        self.minimum,self.maximum=minimum,maximum
        self.last=None
        self.touch(now)

    def touch(self,now):
        self.next_at=now+self.rng.uniform(self.minimum,self.maximum)

    def choose(self,now,busy=False,hovered=False):
        if busy or hovered or now<self.next_at:
            return None
        pool=[x for x in self.actions if x!=self.last]
        self.last=self.rng.choice(pool)
        self.touch(now)
        return self.last

def popup_position(anchor,size,screen,gap=12):
    ax,ay,aw,ah=anchor
    w,h=size
    sx,sy,sw,sh=screen
    x=ax-w-gap if ax-w-gap>=sx else ax+aw+gap
    x=max(sx,min(x,sx+sw-w))
    y=max(sy,min(ay+(ah-h)//2,sy+sh-h))
    return int(x),int(y)
