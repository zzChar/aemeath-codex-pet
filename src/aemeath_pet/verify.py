"""Actual widget flow checks using a separate preferences file."""
import json
from pathlib import Path
import time
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QEnterEvent
from PySide6.QtWidgets import QPushButton, QApplication, QMenu, QLineEdit, QPlainTextEdit
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtTest import QTest
from .app import PetWindow
from .codex import AccountRPC, TaskMonitor, TaskEvent, locate_codex
from .input_state import InputState
from .model import GazeActivity

class TestInputObserver:
    keyboard_ready=False
    def __init__(self):self.state=InputState()
    def sample(self,now):return self.state
    def close(self):pass

class TestSoundEffect:
    def __init__(self):self.play_count=0;self.stop_count=0;self._volume=0
    def setSource(self,source):pass
    def setVolume(self,value):self._volume=value
    def volume(self):return self._volume
    def play(self):self.play_count+=1
    def stop(self):self.stop_count+=1

def smoke_test(app,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    input_observer=TestInputObserver()
    test_sound=TestSoundEffect();opened=[];navigation_success=[False]
    def navigate(tid):opened.append(tid);return navigation_success[0]
    pet=PetWindow(output/"test-state.json",start_worker=False,tray=False,input_observer=input_observer,sound_effect=test_sound,completion_opener=navigate)
    pet.reminder.items=[];pet.reminder.save()
    pet.settings["gaze"]=True
    pet.settings["auto_idle"]=False;pet.show();app.processEvents()
    checks={"permanent_buttons":len(pet.findChildren(QPushButton))}
    assert checks["permanent_buttons"]==0
    assert pet.size().height()==round(208*pet.settings["scale"])
    assert not hasattr(pet,"care") and not hasattr(pet,"game")
    cursor=QCursor.pos()
    center=pet.mapToGlobal(QPoint(pet.width()//2,round(80*pet.settings["scale"])))
    for name,delta in (("up",QPoint(0,-450)),("right",QPoint(450,0)),("down",QPoint(0,450)),("left",QPoint(-450,0))):
        pet.gaze_activity=GazeActivity();pet.gaze_previous=None;pet.director.until=0
        for i in range(4):
            QCursor.setPos(center+delta+QPoint(i*4,0));pet.animate();QTest.qWait(100)
        pet.animate();app.processEvents()
        checks[f"gaze_{name}"]=pet.gaze_previous
    QTest.qWait(450);pet.animate()
    assert pet.gaze_previous is None and pet.rendered_kind=="idle"
    for state,reason in ((InputState(cursor_visible=False),'cursor_hidden'),(InputState(fullscreen=True),'fullscreen'),(InputState(last_key_at=time.monotonic()+10),'typing')):
        input_observer.state=state
        for i in range(4):
            QCursor.setPos(center+QPoint(450,i*4));pet.animate();QTest.qWait(100)
        assert pet.gaze_previous is None and pet.rendered_kind=="idle" and pet.gaze_activity.reason==reason
    checks["gaze_suppression"]=["stationary","cursor_hidden","fullscreen","typing"]
    input_observer.state=InputState()
    QCursor.setPos(cursor)
    assert [checks[f"gaze_{n}"] for n in ("up","right","down","left")]==[0,4,8,12]
    pet.settings["gaze"]=False;pet.settings["fixed_direction"]=8;pet.animate();app.processEvents()
    pet.grab().save(str(output/"pet-only.png"))
    assert pet.pat() and pet.director.current(time.monotonic())=="wave"
    pet.grab().save(str(output/"headpat.png"))
    monitor=TaskMonitor();_,active=monitor.poll()
    pet.on_tasks({"status":monitor.status,"events":[],"active":active,"phase":monitor.phase})
    checks["live_task_status"]=monitor.status
    checks["live_activity_phase"]=monitor.phase
    rpc=AccountRPC(locate_codex())
    try:
        limits=rpc.read_limits();pet.on_limits(limits);checks["real_quota"]=limits
    except Exception as exc:
        checks["quota_error"]=str(exc)
    finally:rpc.close()
    # The hover enter path starts its reveal timer; it is not a click action.
    pet.enterEvent(QEnterEvent(QPoint(100,80),QPoint(100,80),pet.mapToGlobal(QPoint(100,80))))
    assert pet.hover_timer.isActive()
    pet.show_quota();app.processEvents();assert pet.popup.isVisible()
    assert "%" in pet.popup.short.percentage.text() and "%" in pet.popup.week.percentage.text()
    pet.popup.grab().save(str(output/"quota-hover.png"))
    QCursor.setPos(pet.popup.frameGeometry().center());pet.hovered=False;pet.hide_quota_if_outside()
    assert pet.popup.isVisible()  # Moving into the card must not close it.
    QCursor.setPos(QPoint(-10000,-10000));pet.hide_quota_if_outside();assert not pet.popup.isVisible()
    QCursor.setPos(cursor)
    pet.hovered=True;pet.set_setting("hover_quota",False);pet.show_quota();assert not pet.popup.isVisible()
    pet.set_setting("hover_quota",True)
    pet.director.until=0
    for phase,kind in (("thinking","thinking"),("working","workbench")):
        pet.on_tasks({"status":"1 个任务进行中","events":[],"active":[("test","状态显示测试")],"phase":phase})
        app.processEvents()
        assert pet.activity_text()==f"Codex {'思考中' if phase=='thinking' else '工作中'}"
        assert pet.rendered_kind==kind and pet.activity_visible()
        assert pet.height()==round(208*pet.settings["scale"])+30
        pet.grab().save(str(output/f"activity-{phase}.png"))
        assert pet.pat();pet.animate();assert pet.rendered_kind=="wave"
        pet.director.until=0;pet.animate();assert pet.rendered_kind==kind
    pet.set_setting("task_activity",False)
    assert not pet.activity_visible() and pet.height()==round(208*pet.settings["scale"])
    pet.set_setting("task_activity",True);assert pet.rendered_kind=="workbench"
    pet.on_tasks({"status":"Codex 已打开 · 空闲","active":[],"events":[]})
    assert not pet.activity_visible() and pet.height()==round(208*pet.settings["scale"])
    for kind in ("eat","sleep","celebrate"):
        pet.director.start(kind,20,time.monotonic()-({"eat":.45,"sleep":2,"celebrate":.35}[kind]),100)
        pet.animate();app.processEvents();pet.grab().save(str(output/f"animation-{kind}.png"))
        pet.director.until=0
    pet.on_tasks({"status":"Codex 已打开 · 空闲","active":[],"events":[TaskEvent("test","通知流程测试","completed","test")]})
    assert pet.director.current(time.monotonic())=="celebrate"
    assert pet.reminder.latest and pet.reminder_timer.isActive()
    assert "完成" in pet.completion.title.text() and pet.completion.isVisible()
    assert not pet.notice.timer.isActive()  # Completion has no expiry timer.
    repeat_count=test_sound.play_count;pet.reminder_timer.timeout.emit()
    assert test_sound.play_count==repeat_count+1 and pet.reminder.latest and pet.completion.isVisible()
    app.processEvents();pet.completion.grab().save(str(output/"completion-persistent.png"))
    pet.show_quota();assert pet.completion.isVisible() and not pet.popup.isVisible()
    pet.on_tasks({"status":"1 个任务进行中","active":[('another','另一个任务')],"phase":"thinking","events":[TaskEvent('another','另一个任务','started','next')]})
    assert pet.reminder.latest and pet.completion.isVisible()
    pet.pat();assert pet.reminder.latest  # Failed navigation must retain the reminder.
    navigation_success[0]=True
    QTest.mouseClick(pet.completion.go,Qt.LeftButton)
    assert opened[-1]=='test' and not pet.reminder.latest and not pet.reminder_timer.isActive()
    assert not pet.completion.isVisible()
    pet.on_tasks({"status":"Codex 已打开 · 空闲","active":[],"events":[TaskEvent('head-test','摸头查看测试','completed','head-turn')]})
    QTest.mouseClick(pet,Qt.LeftButton,pos=QPoint(round(96*pet.settings['scale']),round(80*pet.settings['scale'])))
    assert opened[-1]=='head-test' and not pet.reminder.latest
    pet.director.until=0;pet.animate();assert pet.gaze_previous==8
    # Select the real menu action through Qt's actual event loop, not a direct settings call.
    def choose_settings():
        menu=QApplication.activePopupWidget()
        assert isinstance(menu,QMenu)
        action=next(a for a in menu.actions() if a.text()=='设置')
        QTest.mouseClick(menu,Qt.LeftButton,pos=menu.actionGeometry(action).center())
    QTimer.singleShot(150,choose_settings)
    pet.context_menu(pet.frameGeometry().center());app.processEvents()
    assert pet.dialog.isVisible() and pet.dialog.windowFlags()&Qt.WindowStaysOnTopHint
    assert QApplication.primaryScreen().availableGeometry().contains(pet.dialog.frameGeometry())
    pet.dialog.volume_slider.setValue(95)
    assert test_sound.volume()==.95 and '95%' in pet.dialog.volume_label.text()
    preview=next(b for b in pet.dialog.findChildren(QPushButton) if b.text()=='试听提示音')
    before=test_sound.play_count;QTest.mouseClick(preview,Qt.LeftButton);assert test_sound.play_count==before+1
    pet.dialog.grab().save(str(output/"settings.png"))
    pet.set_setting("gaze",True)
    assert pet.dialog.checks["gaze"].isChecked()
    pet.set_setting("gaze",False)
    assert not pet.dialog.checks["gaze"].isChecked()
    pet.save();raw=json.loads(pet.statefile.read_text(encoding="utf-8"))
    assert "care" not in raw and raw["version"]==2
    checks["ui_flows"]=["no persistent buttons","sustained-motion gaze","stationary/hidden/fullscreen/typing restores idle","headpat","hover quota","thinking/workbench","completion remains pending","new task cannot erase reminder","failed navigation retains reminder","go button acknowledges after navigation","headpat navigates and acknowledges","right-menu opens settings on top","volume control and preview","settings migration"]
    pet.dialog.close();pet.settings['sound']=False;pet.director.until=0
    pet.on_tasks({'status':'Codex 已打开 · 空闲','active':[],'events':[TaskEvent('report-test','桌宠通知升级','completed','report-turn','已修复设置窗口，并加入音量调节。28 项测试通过。需要你试听提示音。')]})
    app.processEvents()
    assert '音量调节' in pet.completion.detail.text()
    assert not hasattr(pet.completion,'reply') and not hasattr(pet.completion,'send_button')
    assert not pet.completion.findChildren(QLineEdit) and not pet.completion.findChildren(QPlainTextEdit)
    assert pet.completion.findChildren(QPushButton)==[pet.completion.go]
    pet.completion.grab().save(str(output/'work-report-completed.png'))
    QTest.mouseClick(pet.completion.go,Qt.LeftButton)
    assert opened[-1]=='report-test' and not pet.reminder.latest
    pet.on_tasks({'status':'1 个任务进行中','active':[('progress-test','检查通知设置')],'phase':'working','events':[TaskEvent('progress-test','检查通知设置','progress','progress-turn','设置已检查完成，接下来验证声音与窗口显示。')]})
    app.processEvents();assert pet.work_popup.isVisible()
    assert pet.work_popup.expiry.isActive()
    assert pet.work_popup.findChildren(QPushButton)==[pet.work_popup.go]
    assert not pet.work_popup.findChildren(QPlainTextEdit)
    pet.work_popup.grab().save(str(output/'work-report-progress.png'))
    QTest.mouseClick(pet.work_popup.go,Qt.LeftButton)
    assert opened[-1]=='progress-test'
    assert 'quick_reply' not in pet.dialog.checks and 'quick_reply' not in pet.settings
    checks['report_flows']=['final response excerpt','completed card opens corresponding task','progress excerpt and expiry','progress card opens corresponding task','no reply fields or copying controls','no reply setting']
    (output/"verification.json").write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding="utf-8")
    pet.dialog.close();pet.popup.close();pet.notice.close();pet.completion.close();pet.work_popup.close();pet.hide()
    for timer in (pet.timer,pet.save_timer,pet.popup_timer,pet.hover_timer,pet.close_hover,pet.reminder_timer):timer.stop()
    return 0
