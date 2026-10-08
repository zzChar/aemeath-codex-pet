import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QPoint, QRect, QRectF, QLockFile, QUrl
from PySide6.QtGui import QCursor, QIcon, QPixmap, QFont, QFontDatabase, QPainter, QColor, QPen, QLinearGradient, QRegion, QPolygonF, QDesktopServices
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QDialog, QCheckBox, QSlider, QComboBox, QMenu, QSystemTrayIcon, QFileDialog, QMessageBox, QScrollArea
from PySide6.QtMultimedia import QSoundEffect
from .model import load_settings, save_settings, gaze_direction, GazeActivity, AnimationDirector, IdleScheduler, popup_position, CompletionReminder
from .input_state import WindowsInputObserver, InputState
from .codex import AccountRPC, TaskMonitor, locate_codex
from .navigation import thread_url, focus_codex
from .reports import excerpt

STYLE="""
QWidget { font-family:'Microsoft YaHei UI'; font-size:12px; color:#e9e4f5; }
QDialog { background:#292139; }
QLabel#heading { font-size:21px; font-weight:700; color:#ffd2e7; }
QLabel#muted { color:#afa5c4; font-size:11px; }
QPushButton { border:1px solid #5b486d; border-radius:8px; padding:7px 12px; background:#3a2d4e; color:#eedbf0; }
QPushButton:hover { border-color:#ecb0d2; background:#51334f; }
QPushButton:pressed { background:#694661; }
QCheckBox { spacing:10px; padding:5px 0; }
QComboBox { background:#352a47; border:1px solid #564663; padding:6px; border-radius:7px; }
QComboBox QAbstractItemView { background:#352a47; color:#e9e4f5; selection-background-color:#6f4865; }
QSlider::groove:horizontal { background:#463953; height:5px; border-radius:2px; }
QSlider::handle:horizontal { background:#edb0d1; width:14px; margin:-5px 0; border-radius:7px; }
QMenu { background:#2e263d; border:1px solid #6c537b; padding:5px; color:#eadff1; }
QMenu::item { padding:7px 20px; border-radius:5px; }
QMenu::item:selected { background:#61445e; }
"""

def assets_path():
    return Path(sys._MEIPASS)/"assets" if getattr(sys,"frozen",False) else Path(__file__).resolve().parents[2]/"assets"

def data_path():
    return Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"AemeathPet"/"state.json"

def display_title(value,limit=36):
    text=re.sub(r"\[([^\]]+)\]\([^)]+\)",r"\1",value).replace("\n"," ").strip()
    return text[:limit]+("…" if len(text)>limit else "")

def countdown(stamp):
    if not stamp:
        return "重置时间暂不可用"
    seconds=max(0,int(stamp-time.time()))
    if seconds<60:
        return "即将重置 · 等待后台更新"
    h,m=seconds//3600,seconds%3600//60
    return f"{h//24}天 {h%24}小时后重置" if h>=24 else f"{h}小时 {m}分钟后重置"

class CodexWorker(QThread):
    limits=Signal(object)
    failure=Signal(str)
    tasks=Signal(object)

    def __init__(self,executable):
        super().__init__()
        self.rpc=AccountRPC(executable)
        self.monitor=TaskMonitor()
        self.stopping=threading.Event()
        self.refresh=threading.Event()

    def run(self):
        account_thread=threading.Thread(target=self.account_loop,daemon=True)
        account_thread.start()
        while not self.stopping.is_set():
            try:
                events,active=self.monitor.poll()
                self.tasks.emit({"status":self.monitor.status,"events":events,"active":active,"phase":self.monitor.phase,"recent_tasks":self.monitor.recent_tasks})
            except Exception:
                self.tasks.emit({"status":"任务状态暂不可用","events":[],"active":[]})
            self.stopping.wait(1)
        self.rpc.close()
        account_thread.join(3)

    def account_loop(self):
        next_limits=0
        while not self.stopping.is_set():
            if time.monotonic()>=next_limits or self.refresh.is_set():
                self.refresh.clear()
                try:
                    self.limits.emit(self.rpc.read_limits())
                except Exception as exc:
                    self.failure.emit(str(exc) if isinstance(exc,(RuntimeError,TimeoutError)) else "额度读取失败，请稍后刷新")
                next_limits=time.monotonic()+60
            self.stopping.wait(.25)
        self.rpc.close()

    def stop(self):
        self.stopping.set()
        self.rpc.close()
        self.wait(5000)

class CrystalPopup(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(STYLE)

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect=QRectF(2,2,self.width()-4,self.height()-4)
        background=QLinearGradient(rect.topLeft(),rect.bottomRight())
        background.setColorAt(0,QColor(46,32,62,247))
        background.setColorAt(1,QColor(23,30,49,249))
        edge=QLinearGradient(rect.topLeft(),rect.bottomRight())
        edge.setColorAt(0,QColor(243,175,209,205))
        edge.setColorAt(.5,QColor(119,110,156,125))
        edge.setColorAt(1,QColor(130,225,247,180))
        p.setBrush(background)
        p.setPen(QPen(edge,1.2))
        p.drawRoundedRect(rect,17,17)
        p.setPen(QPen(QColor(160,229,247,65),1))
        p.drawLine(23,self.height()-9,self.width()-23,self.height()-9)

    def beside(self,pet):
        screen=QApplication.screenAt(pet.frameGeometry().center()) or QApplication.primaryScreen()
        a=pet.frameGeometry();s=screen.availableGeometry()
        x,y=popup_position((a.x(),a.y(),a.width(),a.height()),(self.width(),self.height()),(s.x(),s.y(),s.width(),s.height()))
        self.move(x,y)

class Gauge(QWidget):
    def __init__(self,color):
        super().__init__()
        self.color=QColor(color)
        self.value=None
        self.setFixedHeight(7)

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255,255,255,18))
        p.drawRoundedRect(QRectF(0,0,self.width(),7),3,3)
        if self.value is not None:
            width=self.width()*max(0,min(100,self.value))/100
            gradient=QLinearGradient(0,0,self.width(),0)
            gradient.setColorAt(0,self.color.darker(120))
            gradient.setColorAt(1,self.color.lighter(115))
            p.setBrush(gradient)
            p.drawRoundedRect(QRectF(0,0,width,7),3,3)

class QuotaRow(QWidget):
    def __init__(self,name,color):
        super().__init__()
        box=QVBoxLayout(self)
        box.setContentsMargins(0,2,0,3)
        box.setSpacing(6)
        top=QHBoxLayout()
        labels=QVBoxLayout()
        labels.setSpacing(2)
        title=QLabel(name)
        title.setStyleSheet(f"color:{color}; font-size:13px; font-weight:600;")
        self.reset=QLabel("正在读取…")
        self.reset.setObjectName("muted")
        labels.addWidget(title);labels.addWidget(self.reset)
        top.addLayout(labels)
        self.percentage=QLabel("—")
        self.percentage.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
        self.percentage.setStyleSheet("font-size:29px; font-weight:600; color:#fff3fa;")
        self.percentage.setMinimumWidth(82)
        top.addWidget(self.percentage)
        box.addLayout(top)
        self.gauge=Gauge(color)
        box.addWidget(self.gauge)

    def update_window(self,window):
        self.gauge.value=window["remaining"] if window else None
        self.gauge.update()
        self.percentage.setText(f"{window['remaining']:g}%" if window else "—")
        self.reset.setText(countdown(window.get("resets_at")) if window else "未返回该窗口的额度")
        self.reset.setToolTip(datetime.fromtimestamp(window["resets_at"]).strftime("%Y-%m-%d %H:%M") if window and window.get("resets_at") else "")

class QuotaPopup(CrystalPopup):
    def __init__(self,pet):
        super().__init__()
        self.pet=pet
        self.setFixedSize(340,302)
        box=QVBoxLayout(self)
        box.setContentsMargins(22,17,22,19)
        box.setSpacing(10)
        header=QHBoxLayout()
        crest=QLabel("◇")
        crest.setStyleSheet("color:#9fe7f2; font-size:26px;")
        header.addWidget(crest)
        words=QVBoxLayout();words.setSpacing(0)
        title=QLabel("爱弥斯 · Codex")
        title.setStyleSheet("font-weight:600; font-size:15px; color:#ffd3e8;")
        subtitle=QLabel("剩余额度")
        subtitle.setObjectName("muted")
        words.addWidget(title);words.addWidget(subtitle)
        header.addLayout(words);header.addStretch()
        settings=QPushButton("设置")
        settings.setFixedSize(44,26)
        settings.setStyleSheet("QPushButton{border:0;background:transparent;padding:0;color:#b4a7c8;} QPushButton:hover{color:#ffcee6;}")
        settings.clicked.connect(pet.open_settings)
        header.addWidget(settings)
        box.addLayout(header)
        self.short=QuotaRow("5 小时额度","#f1afce")
        self.week=QuotaRow("每周额度","#96dfed")
        box.addWidget(self.short);box.addWidget(self.week)
        self.status=QLabel("正在连接 Codex…")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setStyleSheet("color:#c3bbd5; font-size:11px;")
        box.addWidget(self.status)
        footer=QHBoxLayout()
        self.stamp=QLabel("每分钟自动更新")
        self.stamp.setObjectName("muted")
        footer.addWidget(self.stamp);footer.addStretch()
        report_button=QPushButton('工作报告');report_button.setFixedSize(80,26)
        report_button.setStyleSheet('QPushButton{padding:0;border:0;background:transparent;color:#ffd2e7;}')
        report_button.clicked.connect(pet.show_work_report);footer.addWidget(report_button)
        self.refresh_button=QPushButton("刷新")
        self.refresh_button.setFixedSize(47,26)
        self.refresh_button.setStyleSheet("QPushButton{padding:0;border:1px solid #5b5274;background:#352a47;border-radius:7px;color:#d5c7e5;} QPushButton:hover{border-color:#9ddfec;}")
        self.refresh_button.clicked.connect(pet.refresh_quota)
        footer.addWidget(self.refresh_button)
        box.addLayout(footer)
        self.refresh_view()

    def enterEvent(self,event):
        self.pet.close_hover.stop()
        super().enterEvent(event)

    def leaveEvent(self,event):
        self.pet.close_hover.start(340)
        super().leaveEvent(event)

    def refresh_view(self):
        snapshot=self.pet.quota
        windows={w.get("minutes"):w for w in snapshot["windows"]} if snapshot else {}
        self.short.update_window(windows.get(300))
        self.week.update_window(windows.get(10080))
        self.status.setText(self.pet.task_status)
        self.status.setToolTip("\n".join(display_title(title,70) for _,title in self.pet.active_tasks))
        if self.pet.quota_error:
            self.stamp.setText("上次数据 · 刷新失败" if snapshot else "额度连接暂不可用")
            self.stamp.setToolTip(self.pet.quota_error)
        elif snapshot:
            self.stamp.setText(f"更新于 {datetime.fromtimestamp(snapshot['checked_at']):%H:%M:%S} · 每分钟")
            self.stamp.setToolTip("后台额度可能稍有延迟")
        else:
            self.stamp.setText("正在读取当前账号…")

class NoticePopup(CrystalPopup):
    def __init__(self,pet):
        super().__init__()
        self.pet=pet
        self.setFixedSize(275,69)
        box=QVBoxLayout(self);box.setContentsMargins(17,11,17,12);box.setSpacing(1)
        self.title=QLabel();self.title.setTextFormat(Qt.PlainText)
        self.title.setStyleSheet("font-size:13px;font-weight:600;color:#ffcee4;")
        self.subtitle=QLabel();self.subtitle.setObjectName("muted");self.subtitle.setTextFormat(Qt.PlainText)
        box.addWidget(self.title);box.addWidget(self.subtitle)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.hide)

    def present(self,title,subtitle,duration=4500):
        self.title.setText(title);self.subtitle.setText(display_title(subtitle,22))
        self.beside(self.pet)
        self.show();self.timer.start(duration)

class CompletionPopup(CrystalPopup):
    def __init__(self,pet,completion=True):
        super().__init__();self.pet=pet;self.is_completion=completion;self.item={};self.setFixedWidth(380)
        box=QVBoxLayout(self);box.setContentsMargins(18,15,18,16);box.setSpacing(8)
        self.title=QLabel("Codex 已完成工作");self.title.setStyleSheet("font-size:15px;font-weight:600;color:#ffc5de;")
        self.subtitle=QLabel();self.subtitle.setTextFormat(Qt.PlainText);self.subtitle.setObjectName("muted")
        self.detail=QLabel();self.detail.setTextFormat(Qt.PlainText);self.detail.setWordWrap(True);self.detail.setStyleSheet('font-size:13px;color:#e7e1f1;line-height:1.5;')
        self.go=QPushButton("前往查看");self.go.clicked.connect(lambda:pet.review_completion(self.item) if self.is_completion else pet.open_report_task(self.item))
        self.error=QLabel();self.error.setWordWrap(True);self.error.setStyleSheet("color:#ffc5de;font-size:11px;");self.error.hide()
        for widget in (self.title,self.subtitle,self.detail,self.go,self.error):box.addWidget(widget)
        self.expiry=QTimer(self);self.expiry.setSingleShot(True);self.expiry.timeout.connect(self.expire)

    def expire(self):
        if self.underMouse():self.expiry.start(4000)
        else:self.hide()

    def present_report(self,item):
        changed=(self.item.get('thread_id'),self.item.get('turn_id'),self.item.get('summary'))!=(item.get('thread_id'),item.get('turn_id'),item.get('summary'))
        self.item=item.copy();kind=item.get('kind','completed')
        names={'completed':'Codex 已完成工作','progress':'Codex 工作进展'}
        self.title.setText(names.get(kind,'Codex 工作报告'))
        self.subtitle.setText(display_title(item['title'],32));self.subtitle.setToolTip(item['title'])
        summary=item.get('summary') if self.pet.settings['work_reports'] else ''
        self.detail.setText(excerpt(summary,320) or '摸摸头或前往查看，打开对应 Codex 任务。')
        if changed:self.error.hide()
        self.adjustSize();self.beside(self.pet)
        if self.pet.isVisible():self.show()
        if kind=='progress':self.expiry.start(14000)
        else:self.expiry.stop()
        return True

    def present(self):
        latest=self.pet.reminder.latest
        if not latest:return
        count=len(self.pet.reminder.items)
        if not self.present_report(latest):return
        self.title.setText("Codex 已完成工作" if count==1 else f"Codex · {count} 个任务待查看")
        self.go.setText("前往查看" if count==1 else f"前往查看 · 还有 {count} 项")

class SettingsDialog(QDialog):
    def __init__(self,pet):
        super().__init__(pet,Qt.Window|Qt.WindowStaysOnTopHint)
        self.pet=pet
        self.setWindowTitle("爱弥斯 · 陪伴偏好")
        self.setMinimumWidth(390)
        self.setStyleSheet(STYLE)
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0)
        scroll=QScrollArea(self);scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.NoFrame)
        body=QWidget();body.setAutoFillBackground(True)
        palette=body.palette();palette.setColor(palette.ColorRole.Window,QColor('#292139'));body.setPalette(palette);scroll.viewport().setPalette(palette)
        scroll.setWidget(body);outer.addWidget(scroll)
        box=QVBoxLayout(body);box.setContentsMargins(25,23,25,24);box.setSpacing(10)
        title=QLabel("陪伴偏好");title.setObjectName("heading");box.addWidget(title)
        subtitle=QLabel("留一点可爱，其他交给你决定。");subtitle.setObjectName("muted");box.addWidget(subtitle)
        self.checks={}
        for key,label in (("gaze","仅在持续移动鼠标时跟随视线"),("auto_idle","空闲时随机做小动作"),("hover_quota","悬停时显示 Codex 额度"),("task_activity","显示思考 / 工作动画与下方状态"),("task_notifications","显示阶段进展与完成通知"),("work_reports","在卡片显示工作结果摘录"),("progress_reports","工作中显示简短阶段进展（最多每 10 秒一次）"),("sound","完成后重复播放提示音，直到前往查看")):
            control=QCheckBox(label);control.setChecked(pet.settings[key])
            control.toggled.connect(lambda value,k=key:pet.set_setting(k,value))
            box.addWidget(control)
            self.checks[key]=control
        self.volume_label=QLabel(f"提示音量：{round(pet.settings['sound_volume'])}%");box.addWidget(self.volume_label)
        self.volume_slider=QSlider(Qt.Horizontal);self.volume_slider.setRange(0,100);self.volume_slider.setValue(round(pet.settings['sound_volume']))
        self.volume_slider.valueChanged.connect(lambda value:pet.set_setting('sound_volume',value));box.addWidget(self.volume_slider)
        preview=QPushButton("试听提示音");preview.clicked.connect(lambda:pet.play_completion_sound(force=True));box.addWidget(preview)
        audio_note=QLabel("完成提醒每 8 秒一次；点击前往查看或摸头后停止。");audio_note.setObjectName("muted");box.addWidget(audio_note)
        box.addWidget(QLabel("转头范围：大一些更柔和"))
        radius=QSlider(Qt.Horizontal);radius.setRange(80,600);radius.setValue(int(pet.settings["gaze_radius"]))
        radius.valueChanged.connect(lambda value:pet.set_setting("gaze_radius",value));box.addWidget(radius)
        box.addWidget(QLabel("关闭跟随后的固定视线"))
        direction=QComboBox();direction.addItems(["上","右上偏上","右上","右上偏右","右","右下偏右","右下","右下偏下","下","左下偏下","左下","左下偏左","左","左上偏左","左上","左上偏上"])
        direction.setCurrentIndex(pet.settings["fixed_direction"])
        direction.currentIndexChanged.connect(lambda value:pet.set_setting("fixed_direction",value));box.addWidget(direction)
        box.addWidget(QLabel("小人大小"))
        scale=QSlider(Qt.Horizontal);scale.setRange(80,200);scale.setValue(round(pet.settings["scale"]*100))
        scale.valueChanged.connect(lambda value:pet.set_setting("scale",value/100));box.addWidget(scale)
        pick=QPushButton("连接异常时选择 codex.exe");pick.clicked.connect(self.pick_codex);box.addWidget(pick)
        note=QLabel("左键摸头 · 按住拖动 · 悬停看额度\n鼠标停下、打字、全屏或隐藏光标时不跟随。");note.setObjectName("muted");box.addWidget(note)
        done=QPushButton("完成");done.clicked.connect(self.close);box.addWidget(done)
        screen=QApplication.screenAt(pet.frameGeometry().center()) or QApplication.primaryScreen()
        self.resize(440,min(850,screen.availableGeometry().height()-70))

    def pick_codex(self):
        filename,_=QFileDialog.getOpenFileName(self,"选择 Codex CLI","","Codex (codex.exe)")
        if filename:
            self.pet.set_setting("codex_path",filename)
            self.pet.restart_worker()

class PetWindow(QWidget):
    def __init__(self,statefile=None,start_worker=True,tray=True,input_observer=None,sound_effect=None,completion_opener=None):
        super().__init__()
        self.statefile=Path(statefile) if statefile else data_path()
        self.settings=load_settings(self.statefile)
        self.setWindowTitle("爱弥斯 · 像素陪伴")
        self.setWindowFlags(Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(STYLE)
        self.atlas=QPixmap(str(assets_path()/"spritesheet.png"))
        self.extras={kind:QPixmap(str(assets_path()/filename)) for kind,filename in (("eat","eating.png"),("sleep","sleep.png"),("celebrate","celebrate.png"),("workbench","workbench.png"),("thinking","thinking.png"))}
        if self.atlas.isNull() or any(p.isNull() for p in self.extras.values()):
            raise RuntimeError("桌宠动画素材不完整")
        self.frames={}
        for kind,row,count in (("idle",0,6),("wave",3,4),("jump",4,5),("failed",5,8),("stretch",6,6),("working",7,6),("lookaround",8,6)):
            self.frames[kind]=[self.atlas.copy(i*192,row*208,192,208) for i in range(count)]
        for kind,strip in self.extras.items():
            self.frames[kind]=[strip.copy(i*192,0,192,208) for i in range(6)]
        self.look_frames=[self.atlas.copy((i%8)*192,(9+i//8)*208,192,208) for i in range(16)]
        self.quota=None;self.quota_error="";self.task_status="正在连接 Codex…";self.active_tasks=[]
        self.activity_phase=None;self.activity_started=time.monotonic();self.rendered_kind="idle"
        self.worker=None;self.dialog=None
        self.reminder=CompletionReminder(self.statefile.with_name('pending-completions.json'))
        self.completion_opener=completion_opener;self.review_in_progress=False
        self.sound_effect=sound_effect if sound_effect is not None else QSoundEffect(self)
        self.sound_effect.setSource(QUrl.fromLocalFile(str(assets_path()/'completion.wav')))
        self.sound_effect.setVolume(self.settings['sound_volume']/100)
        self.director=AnimationDirector()
        self.gaze_activity=GazeActivity()
        self.input_observer=input_observer or (WindowsInputObserver() if QApplication.platformName()=="windows" else None)
        self.input_state=InputState()
        self.scheduler=IdleScheduler(time.monotonic(),self.settings["idle_min"],self.settings["idle_max"])
        self.frame=0;self.gaze_previous=None;self.current_pixmap=self.frames["idle"][0]
        self.hovered=False;self.menu_open=False;self.dragged=False;self.hover_block_until=0
        self.hover_timer=QTimer(self);self.hover_timer.setSingleShot(True);self.hover_timer.timeout.connect(self.show_quota)
        self.close_hover=QTimer(self);self.close_hover.setSingleShot(True);self.close_hover.timeout.connect(self.hide_quota_if_outside)
        self.popup=QuotaPopup(self)
        self.notice=NoticePopup(self)
        self.completion=CompletionPopup(self)
        self.work_popup=CompletionPopup(self,completion=False)
        self.last_work_report=None;self.last_progress_at=0
        self.recent_tasks=[]
        self.notice.timer.timeout.connect(self.show_quota)
        self.reminder_timer=QTimer(self);self.reminder_timer.setInterval(8000);self.reminder_timer.timeout.connect(self.remind_completion)
        self.resize_pet();self.restore_position()
        self.timer=QTimer(self);self.timer.timeout.connect(self.animate);self.timer.start(100)
        self.save_timer=QTimer(self);self.save_timer.timeout.connect(self.save);self.save_timer.start(30000)
        self.popup_timer=QTimer(self);self.popup_timer.timeout.connect(self.popup.refresh_view);self.popup_timer.start(1000)
        self.tray=None
        if tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray=QSystemTrayIcon(QIcon(self.frames["idle"][0]),self)
            self.tray.setToolTip("爱弥斯 · 双击打开设置")
            menu=QMenu();menu.setStyleSheet(STYLE)
            menu.addAction("显示爱弥斯",self.show_pet);menu.addAction("陪伴偏好",self.open_settings);menu.addAction("退出",self.shutdown)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason:self.open_settings() if reason==QSystemTrayIcon.DoubleClick else None)
            self.tray.show()
        if start_worker:self.restart_worker()
        self.animate()
        if self.reminder.latest:QTimer.singleShot(500,self.remind_completion)

    def resize_pet(self):
        width=round(192*self.settings["scale"]);height=round(208*self.settings["scale"])
        self.sprite_rect=QRect(0,0,width,height)
        self.setFixedSize(width,height+(30 if self.activity_visible() else 0))
        # A stable union avoids hover flicker as individual animation poses move.
        region=QRegion()
        for p in self.look_frames+[frame for frames in self.frames.values() for frame in frames]:
            region=region.united(QRegion(p.scaled(width,height,Qt.IgnoreAspectRatio,Qt.FastTransformation).mask()))
        if self.activity_visible():region=region.united(QRegion(self.activity_rect()))
        self.setMask(region)
        self.ensure_visible()
        if self.popup.isVisible():self.popup.beside(self)
        if self.reminder.latest:self.completion.present()

    def restore_position(self):
        if self.settings["position"]:self.move(*self.settings["position"])
        else:
            rect=QApplication.primaryScreen().availableGeometry()
            self.move(rect.right()-self.width()-30,rect.bottom()-self.height()-24)
        self.ensure_visible()

    def ensure_visible(self):
        screens=QApplication.screens()
        if screens and not any(s.availableGeometry().intersects(self.frameGeometry()) for s in screens):
            rect=screens[0].availableGeometry();self.move(rect.right()-self.width()-30,rect.bottom()-self.height()-24)
        screen=QApplication.screenAt(self.frameGeometry().center())
        if screen:
            rect=screen.availableGeometry()
            self.move(max(rect.left(),min(self.x(),rect.right()-self.width()+1)),max(rect.top(),min(self.y(),rect.bottom()-self.height()+1)))

    def activity_visible(self):
        return bool(self.active_tasks) and self.settings["task_activity"]

    def activity_rect(self):
        return QRect(18,self.sprite_rect.height()+3,self.width()-36,24)

    def activity_text(self):
        return {"thinking":"Codex 思考中","working":"Codex 工作中"}.get(self.activity_phase,"Codex 任务进行中")

    def paintEvent(self,event):
        p=QPainter(self);p.drawPixmap(self.sprite_rect,self.current_pixmap)
        if self.activity_visible():
            p.setRenderHint(QPainter.Antialiasing)
            rect=QRectF(self.activity_rect())
            accent=QColor("#9ee7f4" if self.activity_phase=="working" else "#ffc5de")
            p.setBrush(QColor(38,30,51,235));p.setPen(QPen(QColor(accent.red(),accent.green(),accent.blue(),150),1))
            p.drawRoundedRect(rect,11,11)
            p.setFont(QFont("Microsoft YaHei UI",9));p.setPen(accent)
            p.drawText(rect.adjusted(8,0,-25,0),Qt.AlignCenter,self.activity_text())
            for i in range(3):
                dot=QColor(accent);dot.setAlpha(235 if (self.frame//4)%3==i else 65)
                p.setPen(Qt.NoPen);p.setBrush(dot)
                p.drawEllipse(QRectF(rect.right()-20+i*5,rect.center().y()-1.5,3,3))

    def hit_test(self,global_position):
        return self.isVisible() and self.mask().contains(self.mapFromGlobal(global_position))

    def enterEvent(self,event):
        self.hovered=True;self.close_hover.stop();self.scheduler.touch(time.monotonic())
        if self.settings["hover_quota"] and not self.menu_open:self.hover_timer.start(450)
        super().enterEvent(event)

    def leaveEvent(self,event):
        self.hovered=False;self.hover_timer.stop();self.close_hover.start(340);self.scheduler.touch(time.monotonic())
        super().leaveEvent(event)

    def show_quota(self):
        if not self.settings["hover_quota"] or self.menu_open or time.monotonic()<self.hover_block_until or not self.isVisible():return
        if self.dialog and self.dialog.isVisible():return
        if self.reminder.latest:self.completion.present();return
        if not self.hovered and not self.hit_test(QCursor.pos()):return
        self.notice.hide();self.popup.refresh_view();self.popup.beside(self);self.popup.show()

    def hide_quota_if_outside(self):
        cursor=QCursor.pos()
        if not self.hit_test(cursor) and not (self.popup.isVisible() and self.popup.frameGeometry().contains(cursor)):
            self.popup.hide()

    def mousePressEvent(self,event):
        if event.button()==Qt.RightButton:
            self.context_menu(event.globalPosition().toPoint());return
        if event.button()==Qt.LeftButton:
            self.origin=event.globalPosition().toPoint();self.start_pos=self.pos();self.press_local=event.position().toPoint();self.dragged=False
            self.scheduler.touch(time.monotonic())

    def mouseMoveEvent(self,event):
        if event.buttons()&Qt.LeftButton and hasattr(self,"origin"):
            delta=event.globalPosition().toPoint()-self.origin
            if delta.manhattanLength()>6:
                self.dragged=True;self.popup.hide();self.hover_timer.stop();self.notice.hide();self.move(self.start_pos+delta)
                self.completion.hide()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton and hasattr(self,"press_local"):
            if not self.dragged:
                x,y=self.press_local.x()/self.settings["scale"],self.press_local.y()/self.settings["scale"]
                if 32<=x<=161 and y<132:self.pat()
            else:
                self.save()
                if self.reminder.latest:self.completion.present()

    def pat(self):
        if self.reminder.latest:self.review_completion();return True
        return self.action("wave",2.1,priority=50)

    def action(self,kind,duration,priority=10):
        now=time.monotonic()
        accepted=self.director.start(kind,duration,now,priority)
        if accepted:self.scheduler.touch(now);self.animate()
        return accepted

    def animate(self):
        now=time.monotonic();kind=self.director.current(now)
        cursor_position=QCursor.pos()
        self.input_state=self.input_observer.sample(now) if self.input_observer else InputState()
        follow=self.gaze_activity.sample(now,(cursor_position.x(),cursor_position.y()),self.input_state.cursor_visible,self.input_state.fullscreen,self.input_state.last_key_at)
        if kind and self.active_tasks and self.director.priority<=35:
            self.director.until=0;kind=None
        if not kind and self.settings["auto_idle"]:
            choice=self.scheduler.choose(now,busy=bool(self.active_tasks) or self.menu_open or bool(self.dialog and self.dialog.isVisible()),hovered=self.hovered or self.popup.isVisible())
            if choice:
                self.director.start(choice,self.scheduler.durations[choice],now,10);kind=choice
        if kind:
            frames=self.frames[kind]
            period=.30 if kind=="sleep" else .18 if kind=="eat" else .14
            index=self.director.frame(now,len(frames),period)
            if kind=="sleep":
                elapsed=now-self.director.started
                index=0 if elapsed<.6 else 1+int((elapsed-.6)/.6)%5
            self.current_pixmap=frames[index]
        elif self.activity_visible() and self.activity_phase in ("thinking","working"):
            kind="workbench" if self.activity_phase=="working" else "thinking"
            frames=self.frames[kind];period=.22 if kind=="workbench" else .48
            self.current_pixmap=frames[int((now-self.activity_started)/period)%len(frames)]
        else:
            center=self.mapToGlobal(QPoint(self.width()//2,round(80*self.settings["scale"])))
            cursor=cursor_position-center
            if not self.settings["gaze"]:direction=self.settings["fixed_direction"]
            elif follow:direction=gaze_direction(cursor.x(),cursor.y(),self.settings["gaze_radius"],self.gaze_previous)
            else:direction=None
            self.gaze_previous=direction
            if direction is not None:self.current_pixmap=self.look_frames[direction]
            else:self.current_pixmap=self.frames["working" if self.active_tasks else "idle"][self.frame%6]
        self.rendered_kind=kind or ("gaze" if self.gaze_previous is not None else "idle")
        self.frame+=1;self.update()

    def set_setting(self,key,value):
        self.settings[key]=value
        if self.dialog:
            for k,control in self.dialog.checks.items():
                control.blockSignals(True);control.setChecked(self.settings[k]);control.blockSignals(False)
            self.dialog.volume_slider.blockSignals(True);self.dialog.volume_slider.setValue(round(self.settings['sound_volume']));self.dialog.volume_slider.blockSignals(False)
            self.dialog.volume_label.setText(f"提示音量：{round(self.settings['sound_volume'])}%")
        if key=='sound_volume':self.sound_effect.setVolume(value/100)
        if key=='sound' and not value:self.sound_effect.stop()
        if key=='work_reports':
            if self.completion.isVisible():self.completion.present()
            if self.work_popup.isVisible():self.work_popup.present_report(self.work_popup.item)
        if key=='progress_reports' and not value and self.work_popup.item.get('kind')=='progress':self.work_popup.hide()
        if key in ("scale","task_activity"):self.resize_pet();self.animate()
        if key=="hover_quota" and not value:self.popup.hide();self.hover_timer.stop()
        if key=="auto_idle":
            self.scheduler.touch(time.monotonic())
            if not value and self.director.priority==10:self.director.until=0
        self.save()

    def open_settings(self):
        if self.menu_open:QTimer.singleShot(0,self.open_settings);return
        self.popup.hide();self.hover_timer.stop();self.hover_block_until=time.monotonic()+1
        if not self.dialog:self.dialog=SettingsDialog(self)
        screen=QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        area=screen.availableGeometry();self.dialog.resize(440,min(850,area.height()-70))
        self.dialog.move(area.center()-self.dialog.rect().center())
        self.dialog.show();self.dialog.raise_();self.dialog.activateWindow()

    def context_menu(self,point):
        self.menu_open=True;self.popup.hide();self.notice.hide();self.completion.hide();self.work_popup.hide();self.hover_timer.stop()
        menu=QMenu(self);menu.setStyleSheet(STYLE)
        for key,label in (("gaze","鼠标视线跟随"),("auto_idle","空闲随机动画"),("hover_quota","悬停查看额度"),("task_activity","思考 / 工作动画与状态"),("task_notifications","任务庆祝与提示")):
            item=menu.addAction(label);item.setCheckable(True);item.setChecked(self.settings[key])
            item.triggered.connect(lambda value,k=key:self.set_setting(k,value))
        menu.addSeparator();report_action=menu.addAction('工作报告');settings_action=menu.addAction("设置");menu.addAction("重新居中",self.recenter)
        menu.addAction("暂时隐藏",self.hide_pet if self.tray else lambda:None);menu.addAction("退出",self.shutdown)
        selected=menu.exec(point)
        self.menu_open=False;self.hover_block_until=time.monotonic()+.5;self.scheduler.touch(time.monotonic())
        if self.reminder.latest:self.completion.present()
        if selected==settings_action:self.open_settings()
        elif selected==report_action:self.show_work_report()
        menu.deleteLater()

    def recenter(self):
        rect=QApplication.primaryScreen().availableGeometry();self.move(rect.center()-self.rect().center());self.save()

    def refresh_quota(self):
        if self.worker:self.worker.refresh.set()
        self.popup.stamp.setText("正在刷新…")

    def restart_worker(self):
        if self.worker:self.worker.stop()
        self.worker=CodexWorker(locate_codex(self.settings["codex_path"]))
        self.worker.limits.connect(self.on_limits);self.worker.failure.connect(self.on_failure);self.worker.tasks.connect(self.on_tasks);self.worker.start()

    def on_limits(self,snapshot):
        self.quota=snapshot;self.quota_error="";self.popup.refresh_view()

    def on_failure(self,error):
        self.quota_error=error;self.popup.refresh_view()

    def play_completion_sound(self,force=False):
        if force or self.settings['sound']:
            self.sound_effect.setVolume(self.settings['sound_volume']/100)
            self.sound_effect.play()

    def remind_completion(self):
        if not self.reminder.latest:self.reminder_timer.stop();return
        self.popup.hide();self.notice.hide();self.notice.timer.stop();self.hover_timer.stop()
        if not self.menu_open:
            self.work_popup.hide();self.completion.present()
        self.action('celebrate',4.2,priority=100)
        self.play_completion_sound()
        if not self.reminder_timer.isActive():self.reminder_timer.start()

    def review_completion(self,item=None):
        item=item or self.reminder.latest
        if not item or self.review_in_progress:return
        item=item.copy();self.review_in_progress=True;self.completion.go.setEnabled(False);self.completion.error.hide()
        def finish(success):
            self.review_in_progress=False;self.completion.go.setEnabled(True)
            if not success:
                self.completion.error.setText('未能切回 Codex，请再试一次。');self.completion.error.show();self.completion.present();return
            self.reminder.reviewed(item['thread_id'],item['turn_id']);self.sound_effect.stop()
            self.director.until=0;self.hover_block_until=time.monotonic()+1
            if self.reminder.latest:self.completion.present()
            else:self.reminder_timer.stop();self.completion.hide();self.action('wave',2.1,priority=50)
        if self.completion_opener is not None:
            finish(bool(self.completion_opener(item['thread_id'])));return
        try:accepted=QDesktopServices.openUrl(QUrl(thread_url(item['thread_id'])))
        except (ValueError,OSError):accepted=False
        if not accepted:finish(False);return
        def focus_attempt(remaining=6):
            if focus_codex():finish(True)
            elif remaining>0:QTimer.singleShot(300,lambda:focus_attempt(remaining-1))
            else:finish(False)
        QTimer.singleShot(350,focus_attempt)

    def open_report_task(self,item):
        if not item:return
        latest=self.reminder.latest
        if latest and (latest['thread_id'],latest['turn_id'])==(item['thread_id'],item.get('turn_id')):
            self.review_completion();return
        if self.completion_opener is not None:
            success=bool(self.completion_opener(item['thread_id']))
            if not success:self.work_popup.error.setText('未能打开对应 Codex 任务，请重试。');self.work_popup.error.show()
            return
        try:accepted=QDesktopServices.openUrl(QUrl(thread_url(item['thread_id'])))
        except (ValueError,OSError):accepted=False
        if not accepted:self.work_popup.error.setText('未能打开对应 Codex 任务，请重试。');self.work_popup.error.show();return
        QTimer.singleShot(350,focus_codex)

    def show_work_report(self):
        if self.reminder.latest:self.completion.present();return
        item=self.last_work_report
        if not item and self.recent_tasks:
            tid,title=self.recent_tasks[0]
            item={'thread_id':tid,'title':title,'turn_id':'','kind':'progress','summary':'暂未收到新的阶段报告，可以前往 Codex 查看此任务。'}
        if item:
            self.popup.hide();self.work_popup.present_report(item);self.work_popup.expiry.stop()
        else:self.notice.present('暂无工作报告','等待本机 Codex 的下一步消息。')

    def on_tasks(self,snapshot):
        previously=self.activity_visible()
        self.task_status=snapshot["status"];self.active_tasks=snapshot["active"]
        self.recent_tasks=snapshot.get('recent_tasks',self.recent_tasks)
        phase=snapshot.get("phase") if self.active_tasks else None
        if phase!=self.activity_phase:
            self.activity_phase=phase;self.activity_started=time.monotonic()
        if previously!=self.activity_visible():self.resize_pet()
        if self.settings["task_notifications"]:
            completions=[e for e in snapshot["events"] if e.kind=="completed"]
            if completions:
                fresh=False
                self.work_popup.hide()
                for e in completions:
                    fresh=self.reminder.add(e.thread_id,e.turn_id,e.title,summary=e.summary) or fresh
                    self.last_work_report={'thread_id':e.thread_id,'title':e.title,'turn_id':e.turn_id,'kind':'completed','summary':e.summary}
                title="Codex 任务完成啦" if len(completions)==1 else f"{len(completions)} 个 Codex 任务完成啦"
                if fresh:
                    self.remind_completion()
                    if self.tray:self.tray.showMessage(title,excerpt(completions[-1].summary,220) or display_title(completions[-1].title,72),QSystemTrayIcon.Information,6000)
            progress=[e for e in snapshot['events'] if e.kind=='progress']
            if progress:
                e=progress[-1]
                item={'thread_id':e.thread_id,'title':e.title,'turn_id':e.turn_id,'kind':'progress','summary':e.summary}
                self.last_work_report=item
                now=time.monotonic()
                if self.settings['progress_reports'] and self.settings['work_reports'] and not self.reminder.latest and now-self.last_progress_at>=10 and not self.menu_open:
                    self.last_progress_at=now;self.popup.hide();self.work_popup.present_report(item)
        self.popup.refresh_view()
        self.animate()
        if self.tray:self.tray.setToolTip("爱弥斯 · "+self.task_status)

    def hide_pet(self):
        self.popup.hide();self.notice.hide();self.completion.hide();self.work_popup.hide();self.hide()

    def show_pet(self):
        self.ensure_visible();self.show();self.raise_()
        if self.reminder.latest:self.completion.present()

    def save(self):
        self.settings["position"]=[self.x(),self.y()]
        try:save_settings(self.statefile,self.settings)
        except OSError:pass

    def shutdown(self):
        self.timer.stop();self.save_timer.stop();self.popup_timer.stop();self.hover_timer.stop();self.close_hover.stop();self.reminder_timer.stop()
        self.sound_effect.stop();self.save();self.popup.hide();self.notice.hide();self.completion.hide();self.work_popup.close()
        if self.worker:self.worker.stop();self.worker=None
        if self.input_observer:self.input_observer.close();self.input_observer=None
        if self.tray:self.tray.hide()
        QApplication.quit()

    def closeEvent(self,event):
        self.shutdown();event.accept()

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--smoke-test",type=Path);parser.add_argument("--diagnostic-output",type=Path);parser.add_argument('--open-settings',action='store_true')
    args=parser.parse_args()
    app=QApplication(sys.argv[:1]);app.setApplicationName("AemeathPet");app.setQuitOnLastWindowClosed(False)
    font_dir=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"
    for name in ("msyh.ttc","msyhbd.ttc","seguisym.ttf"):
        if (font_dir/name).is_file():QFontDatabase.addApplicationFont(str(font_dir/name))
    app.setFont(QFont("Microsoft YaHei UI",10));app.setStyle("Fusion")
    if args.smoke_test:
        from .verify import smoke_test
        return smoke_test(app,args.smoke_test)
    target=data_path();target.parent.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(target.parent/"instance.lock"));lock.setStaleLockTime(30000)
    if not lock.tryLock(100):
        QMessageBox.information(None,"爱弥斯已经在这里","请从系统托盘找到她，双击打开设置。")
        return 0
    pet=PetWindow();pet.show()
    if args.open_settings:QTimer.singleShot(500,pet.open_settings)
    if args.diagnostic_output:
        def diagnostics():
            folder=args.diagnostic_output;folder.mkdir(parents=True,exist_ok=True)
            report={"platform":app.platformName(),"task_status":pet.task_status,"activity_phase":pet.activity_phase,"activity_text":pet.activity_text() if pet.activity_visible() else "","rendered_kind":pet.rendered_kind,"active_count":len(pet.active_tasks),"quota":pet.quota,"quota_error":pet.quota_error,"gaze_direction":pet.gaze_previous,"gaze_activity":pet.gaze_activity.reason,"cursor_visible":pet.input_state.cursor_visible,"fullscreen":pet.input_state.fullscreen,"keyboard_observer_ready":bool(pet.input_observer and pet.input_observer.keyboard_ready),"pending_completions":len(pet.reminder.items),"sound_volume":pet.settings['sound_volume'],"audio_ready":pet.sound_effect.status()==QSoundEffect.Ready,"settings_visible":bool(pet.dialog and pet.dialog.isVisible()),"settings_on_top":bool(pet.dialog and pet.dialog.windowFlags()&Qt.WindowStaysOnTopHint),"visible":pet.isVisible(),"size":[pet.width(),pet.height()],"permanent_buttons":sum(b.window() is pet for b in pet.findChildren(QPushButton)),"version":"0.5.1","work_reports_enabled":pet.settings['work_reports'],"notifications_only":True,"report_card_visible":pet.work_popup.isVisible()}
            (folder/"runtime.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            pet.grab().save(str(folder/"pet-running.png"))
            pet.popup.refresh_view();pet.popup.grab().save(str(folder/"quota-popup.png"))
            if pet.dialog and pet.dialog.isVisible():pet.dialog.grab().save(str(folder/'settings-running.png'))
        QTimer.singleShot(15000,diagnostics)
    app.aboutToQuit.connect(pet.save)
    return app.exec()
