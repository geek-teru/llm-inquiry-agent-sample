#!/usr/bin/env python3
"""データセット（資料・問い合わせ管理表・正解データ）の整合を検証する。

Python の標準ライブラリだけで動く。形式は data/README.md を参照。

    python3 scripts/validate_dataset.py            # 開発用のデータを検証する
    python3 scripts/validate_dataset.py --eval     # 評価用（data/eval/）も検証する
    python3 scripts/validate_dataset.py --data-dir path/to/data

まだ無いファイル（CSV・JSONL）は飛ばす。問題が1件でもあれば終了コード 1。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SHEET_COLUMNS = [
    "受付番号",
    "受付日時",
    "チャネル",
    "受付者",
    "会社名",
    "担当者名",
    "連絡先",
    "問い合わせメモ",
    "回答内容",
    "回答者",
    "回答日",
    "対応状況",
    "備考",
]
INQUIRY_ID_RE = re.compile(r"^INQ-\d{4}$")
CHANNELS = {"電話", "チャット", "メール"}
STATUSES = {"未対応", "対応中", "回答済み", "引き継ぎ"}

CASE_TYPES = {"answerable", "no_answer", "insufficient", "needs_human", "confusing", "malicious"}
CATEGORIES = {"pricing_contract", "cancellation", "how_to", "incident", "labor_law", "other"}
ACTIONS = {"answer", "ask_back", "escalate", "reject"}
QUALITIES = {"ok", "outdated", "wrong", "inconsistent"}
DOC_STATUSES = {"current", "obsolete"}

INQUIRY_LABEL_KEYS = ["id", "case_type", "category", "action", "sources", "must_include", "note"]
HISTORY_LABEL_KEYS = ["id", "quality", "note"]


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def info(self, message: str) -> None:
        self.infos.append(message)


@dataclass
class Sheet:
    history: dict[str, dict[str, str]]  # 回答内容が埋まった行（過去の対応履歴）
    inquiries: dict[str, dict[str, str]]  # 回答内容が空の行（新しい問い合わせ）

    @property
    def ids(self) -> set[str]:
        return set(self.history) | set(self.inquiries)


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- 資料


def parse_frontmatter(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return None  # 閉じの --- が無い


def load_docs(docs_dir: Path, base: Path, report: Report) -> dict[str, str]:
    """doc_id -> status を返す。"""
    docs: dict[str, str] = {}
    if not docs_dir.is_dir():
        report.info(f"{rel(docs_dir, base)} が無いので資料の検証を飛ばした")
        return docs
    for path in sorted(docs_dir.glob("*.md")):
        where = rel(path, base)
        meta = parse_frontmatter(path.read_text(encoding="utf-8"))
        if meta is None:
            report.error(where, "frontmatter（--- で囲んだ先頭の部分）が無い")
            continue
        for key in ["doc_id", "title", "category", "version", "valid_from", "valid_until", "status"]:
            if key not in meta:
                report.error(where, f"frontmatter に {key} が無い")
        doc_id = meta.get("doc_id", "")
        if doc_id != path.stem:
            report.error(where, f"doc_id {doc_id!r} がファイル名と一致しない")
        if doc_id in docs:
            report.error(where, f"doc_id {doc_id!r} が重複している")
        status = meta.get("status", "")
        if status not in DOC_STATUSES:
            report.error(where, f"status {status!r} は {sorted(DOC_STATUSES)} のどれでもない")
        for key in ["valid_from", "valid_until"]:
            value = meta.get(key, "")
            if value and not is_date(value, "%Y-%m-%d"):
                report.error(where, f"{key} {value!r} が YYYY-MM-DD でない")
        docs[doc_id] = status
    report.info(f"資料 {len(docs)} 件（obsolete {sum(s == 'obsolete' for s in docs.values())} 件）")
    return docs


# ---------------------------------------------------------------- 管理表


def is_date(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
    except ValueError:
        return False
    return True


def load_sheet(path: Path, base: Path, report: Report, seen_ids: set[str]) -> Sheet | None:
    where = rel(path, base)
    if not path.is_file():
        report.info(f"{where} が無いので飛ばした")
        return None
    sheet = Sheet(history={}, inquiries={})
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        report.error(where, "BOM 付きの UTF-8 になっている（BOM なしで保存する）")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != SHEET_COLUMNS:
            report.error(where, f"1行目の列名が決めた列と違う: {header}")
            return sheet
        for lineno, row in enumerate(reader, start=2):
            at = f"{where}:{lineno}"
            if not any(cell.strip() for cell in row):
                continue  # 空行は読み飛ばす
            if len(row) != len(SHEET_COLUMNS):
                report.error(at, f"列の数が {len(row)}（{len(SHEET_COLUMNS)} のはず）")
                continue
            rec = dict(zip(SHEET_COLUMNS, row))
            check_row_values(rec, at, report)
            inq_id = rec["受付番号"].strip()
            if not INQUIRY_ID_RE.match(inq_id):
                report.error(at, f"受付番号 {inq_id!r} が INQ-0001 の形式でない")
                continue
            if inq_id in seen_ids:
                report.error(at, f"受付番号 {inq_id} が重複している")
                continue
            seen_ids.add(inq_id)
            if rec["回答内容"].strip():
                sheet.history[inq_id] = rec
            else:
                sheet.inquiries[inq_id] = rec
    report.info(f"{where}: 対応履歴 {len(sheet.history)} 件・新しい問い合わせ {len(sheet.inquiries)} 件")
    return sheet


def check_row_values(rec: dict[str, str], at: str, report: Report) -> None:
    received = rec["受付日時"].strip()
    if received and not is_date(received, "%Y-%m-%d %H:%M"):
        report.error(at, f"受付日時 {received!r} が YYYY-MM-DD HH:MM でない")
    answered = rec["回答日"].strip()
    if answered and not is_date(answered, "%Y-%m-%d"):
        report.error(at, f"回答日 {answered!r} が YYYY-MM-DD でない")
    if received and answered and is_date(received, "%Y-%m-%d %H:%M") and is_date(answered, "%Y-%m-%d"):
        if answered < received[:10]:
            report.error(at, f"回答日 {answered} が受付日時 {received} より前")
    channel = rec["チャネル"].strip()
    if channel and channel not in CHANNELS:
        report.error(at, f"チャネル {channel!r} は {sorted(CHANNELS)} のどれでもない")
    status = rec["対応状況"].strip()
    if status and status not in STATUSES:
        report.error(at, f"対応状況 {status!r} は {sorted(STATUSES)} のどれでもない")


# ---------------------------------------------------------------- 正解データ


def load_jsonl(path: Path, base: Path, report: Report, keys: list[str]) -> dict[str, dict] | None:
    where = rel(path, base)
    if not path.is_file():
        report.info(f"{where} が無いので飛ばした")
        return None
    labels: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            at = f"{where}:{lineno}"
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                report.error(at, f"JSON として読めない（{e.msg}）")
                continue
            if not isinstance(obj, dict):
                report.error(at, "1行が JSON のオブジェクトでない")
                continue
            missing = [k for k in keys if k not in obj]
            extra = [k for k in obj if k not in keys]
            if missing:
                report.error(at, f"キーが足りない: {missing}")
            if extra:
                report.error(at, f"決めていないキーがある: {extra}")
            inq_id = obj.get("id")
            if not isinstance(inq_id, str) or not INQUIRY_ID_RE.match(inq_id):
                report.error(at, f"id {inq_id!r} が INQ-0001 の形式でない")
                continue
            if inq_id in labels:
                report.error(at, f"id {inq_id} が重複している")
                continue
            obj["_at"] = at
            labels[inq_id] = obj
    report.info(f"{where}: {len(labels)} 件")
    return labels


def check_one_to_one(rows: set[str], labels: dict[str, dict], what: str, where: str, report: Report) -> None:
    for inq_id in sorted(rows - set(labels)):
        report.error(where, f"{what} {inq_id} の正解が無い")
    for inq_id in sorted(set(labels) - rows):
        report.error(labels[inq_id]["_at"], f"{inq_id} は管理表の{what}に無い")


def check_history_labels(labels: dict[str, dict], report: Report) -> None:
    for obj in labels.values():
        at = obj["_at"]
        if obj.get("quality") not in QUALITIES:
            report.error(at, f"quality {obj.get('quality')!r} は {sorted(QUALITIES)} のどれでもない")
        if not isinstance(obj.get("note", ""), str):
            report.error(at, "note が文字列でない")
        elif obj.get("quality") not in (None, "ok") and not obj.get("note", "").strip():
            report.error(at, "quality が ok 以外なのに note（どこが古い・誤りか）が空")


def check_inquiry_labels(
    labels: dict[str, dict],
    docs: dict[str, str],
    history: dict[str, dict[str, str]] | None,
    history_quality: dict[str, dict] | None,
    inquiry_ids: set[str],
    report: Report,
) -> None:
    for obj in labels.values():
        at = obj["_at"]
        for key, allowed in [("case_type", CASE_TYPES), ("category", CATEGORIES), ("action", ACTIONS)]:
            if obj.get(key) not in allowed:
                report.error(at, f"{key} {obj.get(key)!r} は {sorted(allowed)} のどれでもない")
        for key in ["sources", "must_include"]:
            value = obj.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                report.error(at, f"{key} が空でない文字列の配列でない")
                obj[key] = []
            elif len(value) != len(set(value)):
                report.error(at, f"{key} に同じ値が重複している")
        if not isinstance(obj.get("note", ""), str):
            report.error(at, "note が文字列でない")
        if obj.get("case_type") == "answerable":
            if not obj["sources"]:
                report.error(at, "answerable なのに sources が空")
            if not obj["must_include"]:
                report.error(at, "answerable なのに must_include が空")
        for src in obj["sources"]:
            check_source(src, at, docs, history, history_quality, inquiry_ids, report)


def check_source(
    src: str,
    at: str,
    docs: dict[str, str],
    history: dict[str, dict[str, str]] | None,
    history_quality: dict[str, dict] | None,
    inquiry_ids: set[str],
    report: Report,
) -> None:
    if INQUIRY_ID_RE.match(src):
        if src in inquiry_ids:
            report.error(at, f"sources の {src} は新しい問い合わせ（回答内容が空の行）")
        elif history is None:
            report.error(at, f"sources の {src} を確かめる管理表（対応履歴）が無い")
        elif src not in history:
            report.error(at, f"sources の {src} は対応履歴に無い")
        elif history_quality is not None:
            quality = history_quality.get(src, {}).get("quality")
            if quality is not None and quality != "ok":
                report.error(at, f"sources の {src} は quality が {quality}（ok の履歴だけを根拠にする）")
        return
    if src not in docs:
        report.error(at, f"sources の {src!r} は資料の doc_id にも受付番号にも無い")
    elif docs[src] == "obsolete":
        report.error(at, f"sources の {src} は obsolete の資料")


# ---------------------------------------------------------------- 全体


def validate(data_dir: Path, include_eval: bool) -> Report:
    report = Report()
    base = data_dir.parent
    docs = load_docs(data_dir / "docs", base, report)

    seen_ids: set[str] = set()
    sheet = load_sheet(data_dir / "sheets" / "inquiries.csv", base, report, seen_ids)
    history_labels = load_jsonl(data_dir / "labels" / "history.jsonl", base, report, HISTORY_LABEL_KEYS)
    dev_labels = load_jsonl(data_dir / "labels" / "dev.jsonl", base, report, INQUIRY_LABEL_KEYS)

    history = sheet.history if sheet else None
    inquiry_ids = set(sheet.inquiries) if sheet else set()

    if history_labels is not None:
        check_history_labels(history_labels, report)
        if sheet is not None:
            check_one_to_one(set(sheet.history), history_labels, "対応履歴", rel(data_dir / "labels" / "history.jsonl", base), report)
    if dev_labels is not None:
        check_inquiry_labels(dev_labels, docs, history, history_labels, inquiry_ids, report)
        if sheet is not None:
            check_one_to_one(set(sheet.inquiries), dev_labels, "新しい問い合わせ", rel(data_dir / "labels" / "dev.jsonl", base), report)

    if include_eval:
        eval_dir = data_dir / "eval"
        eval_sheet = load_sheet(eval_dir / "sheets" / "inquiries_eval.csv", base, report, seen_ids)
        eval_labels = load_jsonl(eval_dir / "labels" / "eval.jsonl", base, report, INQUIRY_LABEL_KEYS)
        if eval_sheet is not None:
            for inq_id in sorted(eval_sheet.history):
                report.error(rel(eval_dir / "sheets" / "inquiries_eval.csv", base), f"{inq_id} は回答内容が埋まっている（評価用は新しい問い合わせだけを置く）")
        if eval_labels is not None:
            eval_inquiry_ids = set(eval_sheet.inquiries) if eval_sheet else set()
            check_inquiry_labels(eval_labels, docs, history, history_labels, inquiry_ids | eval_inquiry_ids, report)
            if eval_sheet is not None:
                check_one_to_one(eval_inquiry_ids, eval_labels, "評価用の問い合わせ", rel(eval_dir / "labels" / "eval.jsonl", base), report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / "data", help="data ディレクトリ（既定はリポジトリの data/）")
    parser.add_argument("--eval", action="store_true", help="評価用（data/eval/）も検証する")
    args = parser.parse_args(argv)

    report = validate(args.data_dir.resolve(), args.eval)
    for message in report.infos:
        print(f"[info] {message}")
    for message in report.errors:
        print(f"[error] {message}")
    if report.errors:
        print(f"NG: {len(report.errors)} 件の問題")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
