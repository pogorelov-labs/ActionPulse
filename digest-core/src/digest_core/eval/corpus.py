"""Synthetic replay corpus for the eval harness (PR7).

PRIVACY: every case here is FABRICATED. Never commit real corporate email bodies
into the corpus — only synthetic / redacted content (per ADR-012 and §3.3).

Each case is a frozen ingest snapshot (``<name>.snapshot.json``) plus a committed
metrics baseline (``<name>.baseline.json``). ``write_snapshots`` regenerates the
snapshots deterministically; ``load_corpus`` is what the harness/CLI consume.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from digest_core.ingest.ews import NormalizedMessage

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"


@dataclass(frozen=True)
class Case:
    name: str
    snapshot_path: Path
    baseline_path: Path
    digest_date: str


def _case01_messages() -> List[NormalizedMessage]:
    """One actionable request + one FYI notice (synthetic)."""
    received = datetime(2026, 3, 29, 9, 0, tzinfo=timezone.utc)
    action_body = (
        "Привет! Пожалуйста, подготовь квартальный отчёт по продажам за первый квартал "
        "и пришли его мне до конца недели. Нужны агрегированные цифры по всем регионам, "
        "а также короткое резюме по ключевым отклонениям от плана продаж за прошлый период. "
        "Отдельно добавь, пожалуйста, разбивку по новым и постоянным клиентам и краткий "
        "комментарий по каждому крупному региону отдельной строкой в таблице. Если будут "
        "вопросы по исходным данным или методике расчёта, дай знать заранее, чтобы мы "
        "успели всё перепроверить и согласовать финальную версию до встречи с правлением "
        "в следующий вторник. Заранее спасибо за оперативную подготовку этого материала."
    )
    fyi_body = (
        "Информируем всех сотрудников компании, что в следующий понедельник плановое "
        "техническое обслуживание корпоративного портала и внутренних сервисов пройдёт "
        "с 22:00 до 23:00 по московскому времени. В этот промежуток портал, почтовый "
        "клиент и система согласований будут временно недоступны для всех пользователей. "
        "Никаких действий с вашей стороны не требуется, восстановление произойдёт "
        "автоматически по завершении работ. Просто, пожалуйста, учитывайте это короткое "
        "окно недоступности при планировании вечерней работы с документами и заранее "
        "сохраните все важные черновики, над которыми вы работаете сегодня вечером."
    )
    return [
        NormalizedMessage(
            msg_id="syn-msg-1",
            conversation_id="syn-conv-1",
            datetime_received=received,
            sender_email="manager@example.com",
            subject="Квартальный отчёт по продажам",
            text_body=action_body,
            to_recipients=["me@example.com"],
            cc_recipients=[],
            importance="High",
            body_norm=action_body,
            received_at=received,
        ),
        NormalizedMessage(
            msg_id="syn-msg-2",
            conversation_id="syn-conv-2",
            datetime_received=received,
            sender_email="it-notices@example.com",
            subject="Плановое обслуживание портала",
            text_body=fyi_body,
            to_recipients=["all@example.com"],
            cc_recipients=[],
            importance="Normal",
            body_norm=fyi_body,
            received_at=received,
        ),
    ]


_CASES = {
    "case01": ("2026-03-29", _case01_messages),
}


def write_snapshots(corpus_dir: Path = CORPUS_DIR) -> None:
    """(Re)generate the committed snapshot fixtures from the synthetic messages."""
    from digest_core.run import _dump_ingest_snapshot

    corpus_dir.mkdir(parents=True, exist_ok=True)
    for name, (digest_date, builder) in _CASES.items():
        _dump_ingest_snapshot(corpus_dir / f"{name}.snapshot.json", builder(), digest_date)


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> List[Case]:
    cases: List[Case] = []
    for snapshot in sorted(corpus_dir.glob("*.snapshot.json")):
        name = snapshot.name[: -len(".snapshot.json")]
        meta = json.loads(snapshot.read_text(encoding="utf-8")).get("meta", {})
        cases.append(
            Case(
                name=name,
                snapshot_path=snapshot,
                baseline_path=corpus_dir / f"{name}.baseline.json",
                digest_date=meta.get("digest_date", "today"),
            )
        )
    return cases
