# -*- coding: utf-8 -*-
"""检测 LLM 工具是否正在“思考”。只读文件，不影响被监测的程序。

DSH：  ~/.dsh/storages/session_projcache/sessions/session-*.json
       record.rows.turnBoundary.val.openTurnStartSeq 非空 => 正在回复
WorkBuddy：~/.workbuddy/projects/*/*.jsonl 的末尾记录
       最近有写入，且最后一条不是“助手的纯文本回复” => 正在回复
"""
import glob
import json
import os
import time

HOME = os.path.expanduser("~")
DSH_DIR = os.path.join(HOME, ".dsh", "storages", "session_projcache", "sessions")
WB_DIR = os.path.join(HOME, ".workbuddy", "projects")
CODEX_DIR = os.path.join(HOME, ".codex", "sessions")

DSH_FRESH = 60.0   # 秒：超过则视为崩溃遗留的未关闭回合
DSH_OPEN_GRACE = 3600.0  # 已亲眼见过的长回合，允许中途超过新鲜窗口
WB_FRESH = 8.0     # 秒：transcript 最近写入窗口
WB_GRACE = 3600.0  # 本进程已观察到的同一回合，允许长时间无新写入
CODEX_STALE = 300.0    # 当前 rollout 的任务标记在此窗口内有效
TAIL_BYTES = 16384

# 记录本进程已经观察到的 DSH 回合。dsh_busy 仍严格遵循 60 秒新鲜度；
# Monitor 在同一回合暂时停写时使用此记录保持 active，避免长思考被拆段。
_DSH_SEEN = {}
_WB_SEEN = {}


def _recent(paths, fresh, now):
    for p in paths:
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if now - m <= fresh:
            yield p


def dsh_busy(now=None, base=None):
    now = time.time() if now is None else now
    files = glob.glob(os.path.join(base or DSH_DIR, "session-*.json"))
    for p in _recent(files, DSH_FRESH, now):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            tb = d["record"]["rows"]["turnBoundary"]["val"]
            seq = tb.get("openTurnStartSeq")
            if seq is None:
                _DSH_SEEN.pop(p, None)
                continue
            _DSH_SEEN[p] = (seq, now)
            return True
        except Exception:
            continue  # 写入中途或格式不符，跳过
    # 不让已经删除的会话永久留在内存中。
    for p in list(_DSH_SEEN):
        if not os.path.exists(p):
            _DSH_SEEN.pop(p, None)
    # 当前进程已经确认过的同一 open 回合，即使文件暂时超过 60 秒未写入，
    # 仍保持忙碌，避免长思考被误切成两回合。旧进程遗留的标记不会进入
    # _DSH_SEEN，因此仍遵守 60 秒新鲜度。
    return dsh_grace_busy(now)


def dsh_grace_busy(now=None):
    """已开始的 DSH 回合短暂停写时保持忙碌，供 Monitor 使用。

    只根据本进程曾经读到的同一 openTurnStartSeq 判断，旧进程遗留的
    open 标记不会凭空触发新弹窗；显式关闭后 dsh_busy 会清除记录。
    """
    now = time.time() if now is None else now
    for p, (seq, seen_at) in list(_DSH_SEEN.items()):
        if now - seen_at > DSH_OPEN_GRACE:
            _DSH_SEEN.pop(p, None)
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                tb = json.load(f)["record"]["rows"]["turnBoundary"]["val"]
            if tb.get("openTurnStartSeq") == seq:
                return True
            _DSH_SEEN.pop(p, None)
        except Exception:
            continue
    return False


def _tail_last_record(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        data = f.read()
    lines = data.decode("utf-8", "ignore").splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue  # 被截断的半行或正在写入
        if o.get("type") in ("file-history-snapshot", "session-meta"):
            continue
        # 统计、标题和函数结果不是回合状态，跳过后读取前一条有效记录。
        if o.get("type") in ("credit-usage", "ai-title", "function_call_result"):
            continue
        return o
    return None


def wb_busy(now=None, base=None):
    now = time.time() if now is None else now
    files = glob.glob(os.path.join(base or WB_DIR, "*", "*.jsonl"))
    for p in files:
        try:
            o = _tail_last_record(p)
        except OSError:
            continue
        if o is None:
            continue
        try:
            age = max(0.0, now - os.path.getmtime(p))
        except OSError:
            continue
        if o.get("type") == "message" and o.get("role") == "assistant":
            # 最终助手消息是可靠的回合结束标记；清掉旧缓存，避免下次
            # 轮询仍因同一文件的旧签名触发忙碌。
            _WB_SEEN.pop(p, None)
            continue  # 助手给出最终回复，说明已完成
        # 只签名最后一条有效业务记录；末尾追加 snapshot、统计等被跳过
        # 的记录不应把同一长回合误判成新回合。
        signature = json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        seen = _WB_SEEN.get(p)
        if age <= WB_FRESH:
            _WB_SEEN[p] = (signature, now)
            return True
        # 已经在本进程的新鲜窗口内见过的同一条非助手记录，说明这是
        # 仍未结束的长回合；文件大小或末条记录变化即视为新阶段，重新
        # 进入 8 秒窗口，避免把下一回合误接到上一回合。
        if seen and seen[0] == signature and now - seen[1] <= WB_GRACE:
            return True
        # 未在本进程的新鲜窗口内观察到的旧记录一律视为空闲，避免
        # 历史会话或崩溃遗留的 function_call 凭空触发提醒。正常监测
        # 每 0.8 秒轮询，会在文件新鲜时写入 _WB_SEEN，长工具调用由上方
        # grace 续接。
    for p in list(_WB_SEEN):
        if not os.path.exists(p):
            _WB_SEEN.pop(p, None)
    return False


def _codex_paths(base):
    """只扫固定的 YYYY/MM/DD 深度，不递归进入整个 sessions 目录。"""
    root = base or CODEX_DIR
    return glob.glob(os.path.join(root, "*", "*", "*", "rollout-*.jsonl"))


def _codex_marker(record):
    if record.get("type") == "event_msg":
        payload = record.get("payload") or {}
        return payload.get("type"), payload.get("turn_id")
    return None, None


def codex_busy(now=None, base=None):
    """根据真实 rollout 的 task_started/task_complete 边界判断是否有未完回合。"""
    now = time.time() if now is None else now
    for p in _recent(_codex_paths(base), CODEX_STALE, now):
        try:
            with open(p, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - TAIL_BYTES))
                data = f.read()
        except OSError:
            continue
        active = set()
        for line in data.decode("utf-8", "ignore").splitlines():
            try:
                record = json.loads(line)
            except Exception:
                continue
            marker, turn_id = _codex_marker(record)
            if marker == "task_started":
                active.add(turn_id or "__latest__")
            elif marker in ("task_complete", "task_completed", "turn_aborted", "turn_failed"):
                if turn_id:
                    active.discard(turn_id)
                elif active:
                    active.clear()
        if active:
            return True
    return False


ADAPTERS = {"DSH": dsh_busy, "WorkBuddy": wb_busy, "Codex": codex_busy}


def busy_sources(enabled=None):
    out = []
    for name, fn in ADAPTERS.items():
        if enabled is not None and name not in enabled:
            continue
        try:
            if fn():
                out.append(name)
        except Exception:
            pass
    return out


class Monitor:
    """把原始 busy 信号变成“出现/结束”事件。

    busy 持续 start_after 秒才算开始（过滤短回复）；
    空闲持续 end_after 秒才算结束（过滤工具调用间隙）。
    """

    def __init__(self, probe=busy_sources, start_after=3.0, end_after=4.0):
        self.probe = probe
        self.start_after = start_after
        self.end_after = end_after
        self.active = False
        self._since_busy = None
        self._since_idle = None
        self._active_sources = set()

    def poll(self, now=None, enabled=None):
        """返回 'start' / 'end' / None。"""
        now = time.monotonic() if now is None else now
        sources = self.probe(enabled)
        busy = bool(sources)
        if (not busy and self.active and "DSH" in self._active_sources
                and dsh_grace_busy()):
            busy = True
        if busy:
            if sources:
                self._active_sources.update(sources)
            self._since_idle = None
            if self._since_busy is None:
                self._since_busy = now
            if not self.active and now - self._since_busy >= self.start_after:
                self.active = True
                return "start"
        else:
            self._since_busy = None
            if self._since_idle is None:
                self._since_idle = now
            if self.active and now - self._since_idle >= self.end_after:
                self.active = False
                self._active_sources.clear()
                return "end"
        return None
