# 给代码代理的说明

这是一个 Windows + Python 3.9 的离线桌面小游戏，项目名称为“摸鱼修仙”。如果你使用自己的 Agent 维护本项目，请先通读 `events.py`、`events_chapters.py`、`game.py`、`detector.py`、`tray.py` 和 `xiuxian.py`，再修改代码。

## 常用命令

```powershell
python -m unittest discover -v
python xiuxian.py --demo --data .\\demo_data
```

试玩模式不读取 DSH、WorkBuddy、Codex，也不会使用正式存档。提交前删除 `demo_data` 和个人 `save.json`。

## 必须保持的行为

- 检测器只读 `~/.dsh`、`~/.workbuddy`、`~/.codex` 的会话文件，不得写入被监测程序的数据。
- 自动弹出必须使用 `SW_SHOWNOACTIVATE`，不得抢焦点；托盘手动打开时才允许聚焦。
- 回复结束只改标题为“修仙 · 回复已完成”，不得自动关闭窗口。
- DSH、WorkBuddy、Codex 的交错或同时回复必须合并为同一段忙碌状态。
- 长思考不能被拆成多次；短暂工具调用间隙由 `Monitor` 防抖处理。
- 保持零第三方依赖和中文文案，不要提交个人存档、会话日志或临时文件。

改动检测器时请补构造数据测试；改动界面时请实际启动试玩模式检查窗口、字体、滚动和按钮布局。更多约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。
