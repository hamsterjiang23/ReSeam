"""Prepare uniform external-agent instructions and collect only timely, validated raw reviews."""

import argparse
import json
from pathlib import Path
from typing import Any

from reseam.joint_review import import_review
from reseam.mesh import sha256
from reseam.pipeline import write_json


def prepare(packet: Path, control: Path, reviewers: list[int], attempt: int) -> None:
    if attempt not in (0, 1):
        raise ValueError("Only one retry is permitted")
    clock = json.loads((control / "status.json").read_text(encoding="utf-8"))
    if clock["status"] != "RUNNING":
        raise ValueError("No active review reservation")
    deadline = clock["deadline_unix"] - 30
    template_path = Path(__file__).with_name("joint_review_prompt_template.txt")
    template = template_path.read_text(encoding="utf-8")
    root = packet.parents[2]
    key = packet.parent.name
    raw = root / "raw_reviews" / key
    raw.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for index in reviewers:
        identity = f"{key}_judge{index}_attempt{attempt}"
        output = raw / (identity + ".json")
        if output.exists():
            raise FileExistsError(output)
        prompt = template.format(
            packet=packet.parent.as_posix(),
            reviewer_id=identity,
            packet_sha256=sha256(packet),
            output=output.as_posix(),
            deadline_unix=deadline,
        )
        prompt_file = control / f"prompt_{index}.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        dispatch = f"请执行 {prompt_file.as_posix()} 中的独立盲评任务。只读该任务文件及其明确指定的匿名证据，按要求保存自己的JSON；不要读取仓库、历史或其他评审，不创建子评审。"
        entries.append(
            {
                "reviewer_id": identity,
                "index": index,
                "attempt": attempt,
                "task_name": identity,
                "packet": packet.as_posix(),
                "output": output.as_posix(),
                "prompt_file": prompt_file.as_posix(),
                "prompt_sha256": sha256(prompt_file),
                "dispatch_message": dispatch,
                "agent_id": None,
                "model": "unavailable",
                "runner": "collaboration.spawn_agent",
                "fork_turns": "none",
            }
        )
    registry = {
        "packet": packet.as_posix(),
        "review_deadline_unix": deadline,
        "template_sha256": sha256(template_path),
        "guard": clock,
        "agents": entries,
    }
    if (control / "agents.json").exists():
        raise FileExistsError(control / "agents.json")
    write_json(control / "agents.json", registry)
    print(
        json.dumps(
            {
                "deadline": deadline,
                "agents": [{k: e[k] for k in ("task_name", "dispatch_message")} for e in entries],
            },
            ensure_ascii=False,
        )
    )


def collect(control: Path) -> None:
    registry = json.loads((control / "agents.json").read_text(encoding="utf-8"))
    packet = Path(registry["packet"])
    root = packet.parents[2]
    records = []
    for agent in registry["agents"]:
        raw = Path(agent["output"])
        record = {
            "reviewer_id": agent["reviewer_id"],
            "agent_id": agent["agent_id"],
            "attempt": agent["attempt"],
        }
        try:
            if not raw.exists():
                raise ValueError("MISSING_REVIEW")
            record["finished_file_unix"] = raw.stat().st_mtime
            record["raw_sha256"] = sha256(raw)
            if raw.stat().st_mtime > registry["review_deadline_unix"]:
                raise ValueError("LATE_REVIEW")
            review = json.loads(raw.read_text(encoding="utf-8"))
            if review.get("reviewer_id") != agent["reviewer_id"]:
                raise ValueError("REVIEWER_ID_MISMATCH")
            imported = import_review(packet, raw, root / "reviews" / packet.parent.name)
            record.update(status="VALID_IMPORTED", imported=imported.as_posix())
        except (ValueError, OSError) as error:
            record.update(status="INVALID_OR_MISSING", reason=str(error))
        records.append(record)
    write_json(control / "collection.json", records)
    print(json.dumps(records, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "collect"])
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--reviewers", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.packet is None:
            raise ValueError("Packet is required")
        prepare(args.packet.resolve(), args.control.resolve(), args.reviewers, args.attempt)
    else:
        collect(args.control.resolve())


if __name__ == "__main__":
    main()
