# -*- coding: utf-8 -*-
"""游戏逻辑与存档。不依赖界面，可单独测试。"""
import json
import os
import random
import shutil
import tempfile

import events as ev

SAVE_VERSION = 1
LOG_KEEP = 8
ROOTS = ["金灵根", "木灵根", "水灵根", "火灵根", "土灵根", "风灵根", "雷灵根"]


def need_for(realm, stage):
    """某境界某阶段升到下一阶段所需修为。"""
    return int(80 * (1.35 ** (realm * 4 + stage)))


def fmt_num(n):
    n = int(n)
    if n >= 100000000:
        return "%.1f亿" % (n / 100000000)
    if n >= 10000:
        return "%.1f万" % (n / 10000)
    return str(n)


class Game:
    def __init__(self, save_path, rng=None):
        self.path = save_path
        self.bak = save_path + ".bak"
        self.rng = rng or random.Random()
        self.s = None
        self.load_note = ""

    # ------------------------------------------------------------ 存档
    def _read(self, p):
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or d.get("v") != SAVE_VERSION:
            raise ValueError("bad save")
        for k in ("name", "realm", "stage", "exp", "scene", "log"):
            if k not in d:
                raise ValueError("missing " + k)
        return d

    def load(self):
        """读取存档；主档损坏则读备份；都没有就开新档。返回是否为旧档。"""
        for p, note in ((self.path, ""), (self.bak, "主存档损坏，已从备份恢复。")):
            if os.path.exists(p):
                try:
                    self.s = self._read(p)
                    self.s.setdefault("journey", None)
                    self.s.setdefault("last_turn", None)
                    self.load_note = note
                    return True
                except Exception:
                    continue
        self.new_game()
        return False

    def save(self):
        d = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(prefix="save.", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.s, f, ensure_ascii=False, indent=1)
            if os.path.exists(self.path):
                try:
                    self._read(self.path)  # 只备份完好的存档
                    shutil.copyfile(self.path, self.bak)
                except Exception:
                    pass
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def new_game(self, keep_ui=None):
        r = self.rng
        name = r.choice(ev.NAMES)
        root = r.choice(ROOTS)
        self.s = {
            "v": SAVE_VERSION, "name": name, "root": root,
            "realm": 0, "stage": 0, "exp": 0,
            "luck": 20, "bad": 0, "stones": 30,
            "turn": 0, "rebirth": 0, "kills": 0, "slaps": 0, "breaks": 0,
            "recent": [], "log": [], "scene": None,
            "journey": None, "last_turn": None,
            "ui": keep_ui or {},
        }
        intro = ev.INTRO.format(sect=ev.SECT, root=root, name=name)
        self.s["log"].append(intro)
        self._next_scene()
        self.save()

    def reset(self):
        self.new_game(keep_ui=self.s.get("ui") if self.s else None)

    # ------------------------------------------------------------ 状态
    @property
    def need(self):
        return need_for(self.s["realm"], self.s["stage"])

    def header(self):
        s = self.s
        r = min(s["realm"], len(ev.REALMS) - 1)
        title = "%s  ·  第%d世  ·  %s弟子" % (s["name"], s["rebirth"] + 1, ev.SECT)
        realm = "%s期 %s" % (ev.REALMS[r], ev.STAGES[s["stage"]])
        need = self.need
        pct = min(100, int(s["exp"] * 100 / need)) if need else 0
        prog = "修为 %d%% · %s / %s" % (pct, fmt_num(s["exp"]), fmt_num(need))
        if s["stage"] == 3 and s["exp"] >= need:
            prog += "  【可突破】"
        luck = s["luck"]
        stat = "气运 %d   灵石 %s   机缘 %d 回合" % (luck, fmt_num(s["stones"]), s["turn"])
        return [title, realm, prog, stat]

    def story_text(self):
        return "\n\n· · ·\n\n".join(self.s["log"])

    def options(self):
        return [o["label"] for o in self.s["scene"]["opts"]]

    # ------------------------------------------------------------ 场景
    def _tier(self):
        return 0 if self.s["realm"] < 2 else (1 if self.s["realm"] < 4 else 2)

    def _bind(self):
        r, t, s = self.rng, self._tier(), self.s
        npc = r.choice(ev.NPC)
        npc2 = r.choice([n for n in ev.NPC if n != npc])
        return dict(
            name=s["name"], realm=ev.REALMS[min(s["realm"], 8)] + "期",
            foe_realm=ev.REALMS[min(s["realm"] + 1, 8)] + "期",
            npc=npc, npc2=npc2, place=r.choice(ev.PLACE[t]),
            foe=r.choice(ev.FOE[t]), item=r.choice(ev.ITEM[t]),
            beast=r.choice(ev.BEAST[t]), pill=ev.PILL[t],
            sect=ev.SECT, tech=r.choice(ev.TECH[t]),
        )

    def _break_cost(self):
        return 40 * (self.s["realm"] + 1)

    def _next_scene(self):
        s = self.s
        b = self._bind()
        if s["stage"] == 3 and s["exp"] >= self.need:
            s["journey"] = None
            tpl = ev.TRIB if s["realm"] >= 8 else ev.BREAK
            cost = self._break_cost()
            b["target"] = ev.REALMS[s["realm"] + 1] if s["realm"] < 8 else "仙"
            b["cost"] = cost
            opts = [{"kind": k, "label": lab.format(**b), "ok": ok.format(**b),
                     "ko": ko.format(**b)} for k, lab, ok, ko in tpl["opts"]]
            scene = {"kind": "break", "id": "break", "flag": None,
                     "text": tpl["text"].format(**b), "opts": opts}
        else:
            eligible = [e for e in ev.EVENTS
                        if e["min_realm"] <= s["realm"] <= e["max_realm"]]
            journey = s.get("journey")
            if journey and journey["realm"] != s["realm"]:
                journey = s["journey"] = None
            if journey:
                e = next(e for e in eligible if e["id"] == journey["events"][journey["step"]])
                b = dict(journey["bind"])
            else:
                # 每篇至多三回合；保留少量独立奇遇调剂节奏。
                starts = [e for e in eligible if e.get("step", 0) == 0]
                pool = [e for e in starts if e["id"] not in s["recent"]] or starts
                chapters = [e for e in pool if e.get("chapter")]
                e = self.rng.choice(chapters if chapters and (s["turn"] == 0 or self.rng.random() < 0.85) else pool)
                if e.get("chapter"):
                    chapter = ev.CHAPTERS[e["chapter"]]
                    journey = s["journey"] = {
                        "id": e["chapter"], "events": chapter["events"], "step": 0,
                        "realm": s["realm"], "bind": dict(b),
                    }
            opts = []
            for k, lab, ok, ko in e["opts"]:
                opts.append({"kind": k, "label": lab.format(**b), "ok": ok.format(**b),
                             "ko": ko.format(**b) if ko else None})
            scene = {"kind": "event", "id": e["id"], "flag": e["flag"],
                     "text": e["text"].format(**b), "opts": opts,
                     "chapter": e.get("chapter"), "title": e.get("title", "山中偶遇"),
                     "step": e.get("step", 0), "total": e.get("total", 1)}
            s["recent"] = (s["recent"] + [e["id"]])[-10:]
        s["scene"] = scene
        if s.get("last_turn") and s["bad"] and scene.get("chapter"):
            scene["text"] = "同伴接手善后，你调息片刻再行。" + scene["text"]
        self._log(scene["text"])

    def _log(self, text):
        self.s["log"].append(text)
        del self.s["log"][:-LOG_KEEP]

    # ------------------------------------------------------------ 结算
    def _chance(self, base):
        return min(0.95, base + self.s["luck"] * 0.002)

    def _roll(self, kind, base):
        """返回 (成功?, 是否保底触发)。"""
        s = self.s
        if kind == "safe":
            return True, False
        if s["bad"] >= 2:
            return True, True
        return self.rng.random() < self._chance(base), False

    def _gain(self, frac):
        s = self.s
        mult = 1 + 0.1 * s["rebirth"]
        return int(self.need * frac * mult)

    def _add_exp(self, amount):
        """加修为并处理自动升小境界，返回升阶提示文字列表。"""
        s = self.s
        ups = []
        s["exp"] += amount
        while s["stage"] < 3 and s["exp"] >= self.need:
            s["exp"] -= self.need
            s["stage"] += 1
            realm = ev.REALMS[s["realm"]] + "期"
            ups.append(self.rng.choice(ev.STAGE_UP).format(realm=realm, stage=ev.STAGES[s["stage"]]))
        if s["stage"] == 3 and s["exp"] > self.need:
            s["exp"] = self.need
        return ups

    def choose(self, idx):
        s = self.s
        sc = s["scene"]
        if idx < 0 or idx >= len(sc["opts"]):
            return
        opt = sc["opts"][idx]
        if sc["kind"] == "break":
            lines = self._resolve_break(opt)
        else:
            lines = self._resolve_event(sc, opt)
        s["turn"] += 1
        finished = sc.get("step", 0) + 1 >= sc.get("total", 1)
        s["last_turn"] = {"text": sc["text"], "choice": opt["label"],
                          "result": "\n".join(lines), "finished": finished,
                          "title": sc.get("title", "叩问仙途")}
        if s.get("journey"):
            if finished:
                s["journey"] = None
            else:
                s["journey"]["step"] += 1
        self._log("\n".join(lines))
        self._next_scene()
        self.save()

    def _resolve_event(self, sc, opt):
        s, r = self.s, self.rng
        kind = opt["kind"]
        base = {"safe": 1.0, "risky": 0.62, "gamble": 0.38}[kind]
        ok, pity = self._roll(kind, base)
        mult_lo, mult_hi = {"safe": (0.10, 0.15), "risky": (0.18, 0.30), "gamble": (0.30, 0.48)}[kind]
        out = []
        tags = []
        if ok:
            if pity:
                tags.append(ev.PITY_HINT)
            out.append(opt["ok"])
            frac = r.uniform(mult_lo, mult_hi)
            crit_p = (0.04 if kind == "safe" else 0.08) + s["luck"] * 0.001
            if r.random() < crit_p:
                frac *= 2.2
                tags.append(r.choice(ev.CRIT))
                s["luck"] = max(0, s["luck"] - 25)
            gain = max(1, self._gain(frac))
            stones = r.randint(8, 20) * (s["realm"] + 1) * (2 if kind == "gamble" else 1)
            s["stones"] += stones
            s["bad"] = 0
            s["luck"] = min(100, s["luck"] + (2 if kind == "safe" else 6))
            if sc.get("flag") == "cross" and kind == "gamble":
                s["kills"] += 1
                tags.append(ev.CROSS_KILL)
            if sc.get("flag") == "slap" and kind != "safe":
                s["slaps"] += 1
                tags.append(r.choice(ev.SLAP))
            if s["luck"] >= 80 and r.random() < 0.3:
                tags.append(ev.LUCK_HIGH)
            ups = self._add_exp(gain)
            out = tags[:1] + out if pity else out
            rest = [t for t in tags if not (pity and t == ev.PITY_HINT)]
            out.extend(rest)
            out.extend(ups)
            out.append("修为 +%s    灵石 +%s" % (fmt_num(gain), fmt_num(stones)))
        else:
            out.append(opt["ko"])
            loss = min(s["exp"], int(self.need * 0.03))
            s["exp"] -= loss
            s["bad"] += 1
            s["luck"] = min(100, s["luck"] + 5)
            if s["bad"] >= 2:
                out.append(ev.PITY_NEXT)
            out.append("修为 -%s    （气运 +5）" % fmt_num(loss) if loss else "无大碍。（气运 +5）")
        return out

    def _resolve_break(self, opt):
        s, r = self.s, self.rng
        kind = opt["kind"]
        cost = self._break_cost()
        out = []
        if kind == "safe":
            if s["stones"] >= cost:
                s["stones"] -= cost
                ok, pity = True, False
                out.append("耗去灵石 %s。" % fmt_num(cost))
            else:
                out.append("灵石不足，药力只发挥了七成。")
                s["stones"] = 0
                ok, pity = r.random() < 0.7 or s["bad"] >= 2, s["bad"] >= 2
        else:
            ok, pity = self._roll(kind, 0.70 if kind == "risky" else 0.45)
        if ok:
            if pity:
                out.append(ev.PITY_HINT)
            out.append(opt["ok"])
            s["bad"] = 0
            s["breaks"] += 1
            new = s["realm"] + 1
            if new > 8:
                return out + self._ascend()
            s["realm"], s["stage"], s["exp"] = new, 0, 0
            out.append(ev.BREAK_OK[new])
            out.append("【突破】%s期  ·  %s" % (ev.REALMS[new], ev.STAGES[0]))
            if kind == "gamble":
                bonus = int(self.need * 0.35)
                s["exp"] = bonus
                s["luck"] = min(100, s["luck"] + 20)
                out.append("险中求胜，根基远胜常人。修为 +%s，气运 +20" % fmt_num(bonus))
            else:
                s["luck"] = min(100, s["luck"] + 8)
        else:
            out.append(opt["ko"])
            loss = min(s["exp"], int(self.need * 0.08))
            s["exp"] -= loss
            s["bad"] += 1
            s["luck"] = min(100, s["luck"] + 10)
            if s["bad"] >= 2:
                out.append(ev.PITY_NEXT)
            out.append("修为 -%s    （气运 +10）可再次尝试。" % fmt_num(loss))
        return out

    def _ascend(self):
        s = self.s
        s["rebirth"] += 1
        s["realm"], s["stage"], s["exp"] = 0, 0, 0
        s["luck"] = 50
        s["bad"] = 0
        text = ev.ASCEND_TEXT.format(name=s["name"])
        return [text, "第%d世开启，此后修炼速度 +10%%。" % (s["rebirth"] + 1)]

    # ------------------------------------------------------------ 其他
    def welcome(self):
        """启动时插入到当前场景之前的欢迎语（只在内存中显示）。"""
        note = self.load_note + " " if self.load_note else ""
        return note + ev.WELCOME_BACK.format(name=self.s["name"])
