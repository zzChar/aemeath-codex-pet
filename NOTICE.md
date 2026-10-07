# 素材与依赖说明

本项目是非官方的爱弥斯像素桌宠。人物主体和视线素材源于提供的《鸣潮》游戏像素 GIF。角色及游戏美术权利归原权利人，本项目不声称取得角色或游戏素材的商业使用、再许可或其他分发授权。这些素材不属于项目代码或依赖的开源许可证范围。

吃蛋糕、睡觉、庆祝、工作台、思考六帧动画使用 OpenAI imagegen 参照人物补充生成；生成源图保留在 assets/*-source.png，提示词见 asset-prompts.txt、activity-prompts.txt。衍生角色形象仍受原角色权利约束。completion.wav 为项目原创合成音频，生成脚本随源码提供。

界面与桌宠逻辑为本项目新增代码；本仓库暂未为项目自身代码授予通用开源许可。GitHub 公开可见不表示所有文件均可自由商业使用。依赖的既有许可和权利不受此说明限制。

交互设计参考 https://github.com/A8Chann/dsh-pet-live2d 。未复制其实现代码或鲸鱼娘美术。

使用 PySide6 / Shiboken6 / Qt 6.11.2，其许可选项包含 LGPLv3/GPL/商业许可。本便携包使用动态 Qt 库并附项目源码、构建说明和许可证文件，用户可以替换兼容的动态库。依赖源码与文档：

- PySide / Shiboken：https://code.qt.io/cgit/pyside/pyside-setup.git/ （对应版本 6.11.2）
- Qt：https://code.qt.io/ （对应版本 6.11.2）
- 文档：https://doc.qt.io/qtforpython-6/

Qt Multimedia 动态使用 FFmpeg；许可信息见 licenses/FFmpeg-NOTICE.txt 和 LGPL-2.1.txt。psutil 7.2.2 使用 BSD-3-Clause；PyInstaller 使用 GPL 并具有打包应用例外；Python 许可随包提供。licenses/ 收录依赖随发行包提供的许可与元数据；其中商业许可引用文件不代表本项目取得商业许可。

字体仅从 Windows 本机加载，没有在本项目中打包或分发。
