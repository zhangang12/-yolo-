#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则引擎 rule_engine.py
读 rules/rules.json，对结构化数据逐条评估，输出违规清单。

用法:
  python rule_engine.py --dry-run rules/rules.json
      # 只解析规则、校验字段齐全
  python rule_engine.py rules/rules.json --data examples/structured.json
      # 用样例结构化数据跑全部规则

结构化数据格式见 rules/schema.md。
"""
import json
import re
import argparse
from pathlib import Path


# ---------- 算子表 ----------
OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}

_MISSING = object()  # 哨兵，标记字段不存在


# ---------- 路径解析 ----------
def _resolve(path, contexts):
    """path 例如 'target.area_m2' / 'station.type' / 'item.zone'。
    contexts = {'target': ..., 'station': ..., 'item': ...}。
    任一段缺失返回 _MISSING。"""
    parts = path.split('.')
    if not parts or parts[0] not in contexts:
        return _MISSING
    cur = contexts[parts[0]]
    for p in parts[1:]:
        if cur is None:
            return _MISSING
        if isinstance(cur, dict):
            if p not in cur:
                return _MISSING
            cur = cur[p]
        else:
            if not hasattr(cur, p):
                return _MISSING
            cur = getattr(cur, p)
    return cur


# ---------- 条件评估 ----------
def _check_cond(cond, contexts):
    """评估单条 applies_when / filter 条件。
    字段缺失 → 视为不满足（返回 False），由上层 applies_when 决定整体行为。"""
    val = _resolve(cond['path'], contexts)
    if val is _MISSING:
        return False
    op = OPS.get(cond['op'])
    if op is None:
        raise ValueError(f"未知算子 op={cond['op']}")
    return op(val, cond['value'])


def _applies(rule, contexts):
    for c in rule.get('applies_when', []):
        if not _check_cond(c, contexts):
            return False
    return True


# ---------- 判据评估 ----------
def _eval_check(rule, contexts):
    """返回 {'passed', 'value', 'threshold', 'review_required', 'reason'}。
    passed=None 且 review_required=True 表示数据缺失/类型不符，需人工复核。"""
    check = rule['check']
    ctype = check.get('type', 'compare')
    op = check['op']

    if ctype == 'compare':
        threshold = check.get('threshold')
        val = _resolve(check['path'], contexts)
        if val is _MISSING or val is None:
            return dict(passed=None, value=None, threshold=threshold,
                        review_required=True, reason=f"缺少字段 {check['path']}")
        try:
            passed = OPS[op](val, threshold)
        except TypeError as e:
            return dict(passed=None, value=val, threshold=threshold,
                        review_required=True, reason=f"类型不匹配: {e}")
        return dict(passed=bool(passed), value=val, threshold=threshold,
                    review_required=False, reason=None)

    if ctype == 'count':
        threshold = check.get('threshold')
        coll = _resolve(check['collection_path'], contexts)
        if coll is _MISSING or coll is None:
            return dict(passed=None, value=None, threshold=threshold,
                        review_required=True, reason=f"缺少集合 {check['collection_path']}")
        if not isinstance(coll, (list, tuple)):
            return dict(passed=None, value=None, threshold=threshold,
                        review_required=True, reason=f"{check['collection_path']} 不是数组")
        filters = check.get('filter', [])
        count = 0
        for item in coll:
            item_ctx = dict(contexts)
            item_ctx['item'] = item
            if all(_check_cond(f, item_ctx) for f in filters):
                count += 1
        passed = OPS[op](count, threshold)
        return dict(passed=bool(passed), value=count, threshold=threshold,
                    review_required=False, reason=None)

    raise ValueError(f"未知 check.type={ctype}")


# ---------- 消息模板 ----------
_PH = re.compile(r"\{([\w\.]+)\}")


def _render(template, target, value, threshold):
    def repl(m):
        key = m.group(1)
        if key == 'value':
            return str(value)
        if key == 'threshold':
            return str(threshold)
        if key.startswith('target.'):
            v = _resolve(key, {'target': target})
            return str(v) if v is not _MISSING else f"{{{key}}}"
        return f"{{{key}}}"
    return _PH.sub(repl, template)


# ---------- 主入口 ----------
def load_rules(rules_path):
    with open(rules_path, 'r', encoding='utf-8') as f:
        spec = json.load(f)
    return spec


def evaluate(rules_path, structured):
    """对 structured 跑全部规则。

    structured 至少包含：
        station: dict (可选键: type, height_m, transfer_lines, public_zone, equipment_zone)
        fire_compartment / evac_distance_line / door / exit_pair / corridor /
        shop / shop_pair / vent_pair: 列表

    返回 list[finding]。
    """
    spec = load_rules(rules_path)
    station = structured.get('station') or {}
    findings = []
    for rule in spec['rules']:
        ttype = rule['target']
        if ttype == 'station':
            targets = [station] if station else []
        else:
            targets = structured.get(ttype) or []
        for tgt in targets:
            ctx = {'target': tgt, 'station': station}
            if not _applies(rule, ctx):
                continue
            r = _eval_check(rule, ctx)
            tid = tgt.get('id') if isinstance(tgt, dict) else None
            msg = _render(rule['message'], tgt, r['value'], r['threshold'])
            if r['review_required']:
                msg = f"{msg}  [待人工复核: {r['reason']}]"
            findings.append(dict(
                rule_id=rule['rule_id'],
                name=rule['name'],
                category=rule['category'],
                target_type=ttype,
                target_id=tid,
                passed=r['passed'],
                review_required=r['review_required'],
                value=r['value'],
                threshold=r['threshold'],
                severity=rule['severity'],
                mandatory=rule['mandatory'],
                source=rule['source'],
                message=msg,
            ))
    return findings


def summarize(findings):
    return dict(
        total=len(findings),
        passed=sum(1 for f in findings if f['passed'] is True),
        failed=sum(1 for f in findings if f['passed'] is False),
        review_required=sum(1 for f in findings if f['review_required']),
    )


def from_e2e_flat(e2e_data, station=None):
    """适配旧版 e2e_demo 的扁平结构 → 新引擎结构。
    e2e_data: {compartment_area_labels_m2: [..], evac_distance_values_m: [..], ...}
    station: 站点元数据 dict，默认为单线地下站；未提供 public_zone/equipment_zone 时
             EXIT-NUM/SHOP 等聚合检查会返回 review_required 而非误报失败。
    注意：分区类型未知，按 'public' 处理；只触发公共区规则。真实使用前应替换为人工/自动标注的真值。
    """
    s = station or dict(type='underground', height_m=0, transfer_lines=1)
    fcs = []
    for i, a in enumerate(e2e_data.get('compartment_area_labels_m2') or []):
        fcs.append(dict(id=f"FC-{i+1:02d}", area_m2=float(a),
                        zone_type='public', is_shared_concourse=False,
                        _from='e2e_flat_guess'))
    lines = []
    for i, d in enumerate(e2e_data.get('evac_distance_values_m') or []):
        lines.append(dict(id=f"EVAC-{i+1:02d}", length_m=float(d),
                          kind='any_to_exit', _from='e2e_flat_guess'))
    return dict(station=s, fire_compartment=fcs, evac_distance_line=lines)


# ---------- CLI ----------
def cmd_dry_run(rules_path):
    spec = load_rules(rules_path)
    required = ['rule_id', 'name', 'category', 'target', 'applies_when',
                'check', 'mandatory', 'severity', 'source', 'message']
    errs = []
    ids = set()
    for r in spec['rules']:
        rid = r.get('rule_id', '?')
        for k in required:
            if k not in r:
                errs.append(f"{rid}: 缺字段 {k}")
        if rid in ids:
            errs.append(f"{rid}: rule_id 重复")
        ids.add(rid)
        # check 子字段
        ch = r.get('check', {})
        if 'type' not in ch and 'op' not in ch:
            errs.append(f"{rid}: check 缺 op/type")
        # op 合法性
        if ch.get('op') and ch['op'] not in OPS:
            errs.append(f"{rid}: 未知 check.op={ch['op']}")
        for c in r.get('applies_when', []):
            if c.get('op') and c['op'] not in OPS:
                errs.append(f"{rid}: 未知 applies_when.op={c['op']}")
    print(f"[dry-run] 规则 {len(spec['rules'])} 条, 错误 {len(errs)} 项")
    for e in errs:
        print('   X', e)
    return 0 if not errs else 1


def cmd_run(rules_path, data_path, json_out=None):
    with open(data_path, 'r', encoding='utf-8') as f:
        structured = json.load(f)
    findings = evaluate(rules_path, structured)
    summ = summarize(findings)
    print(f"== 规则评估: {len(load_rules(rules_path)['rules'])} 条规则, 触发 {summ['total']} 项 ==")
    print(f"   PASS {summ['passed']}   FAIL {summ['failed']}   待复核 {summ['review_required']}\n")
    for f in findings:
        if f['passed'] is False:
            icon = 'X'
        elif f['review_required']:
            icon = '?'
        else:
            icon = 'OK'
        m = '强' if f['mandatory'] else '宜'
        print(f"  [{icon}|{m}] {f['rule_id']}  {f['message']}")
        print(f"          来源: {f['source']}")
    if json_out:
        with open(json_out, 'w', encoding='utf-8') as f:
            json.dump(dict(summary=summ, findings=findings), f,
                      ensure_ascii=False, indent=2)
        print(f"\n[JSON] -> {json_out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description='Fire-code rule engine')
    ap.add_argument('rules', help='rules/rules.json')
    ap.add_argument('--data', help='结构化数据 JSON')
    ap.add_argument('--dry-run', action='store_true', help='只校验规则文件')
    ap.add_argument('--out', help='把结果写到 JSON 文件')
    args = ap.parse_args()
    if args.dry_run:
        return cmd_dry_run(args.rules)
    if not args.data:
        ap.error('需要 --data 或 --dry-run')
    return cmd_run(args.rules, args.data, args.out)


if __name__ == '__main__':
    raise SystemExit(main())
