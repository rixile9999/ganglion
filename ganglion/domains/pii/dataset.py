"""Seeded synthetic PII records grouped by template family, with exact spans."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import re

RECIPES = ("legacy", "diverse-v1")

NAMES = ("김민수", "박지영", "이서준", "최유진", "정하늘", "오지훈", "한수빈", "강민지", "서도윤", "윤서연", "배준호", "송지우")
ADDRESSES = ("서울특별시 강남구 테헤란로 123 4층", "부산광역시 해운대구 센텀로 45", "경기도 수원시 영통로 72 101호", "인천광역시 연수구 송도길 18", "대전광역시 유성구 대학로 99")
FAMILIES = {
    "train": ("이름: {PERSON}\n주소: {ADDRESS}\n전화: {PHONE}\n이메일: {EMAIL}",
              "{PERSON}님께 연락해주세요. 연락처는 {PHONE}입니다.",
              "저는 {PERSON}입니다. 배송지: {ADDRESS}",
              "담당자: {PERSON}\n이메일: {EMAIL}\n식별번호: {IDENTIFIER}",
              "고객명: {PERSON}\n주소: {ADDRESS}\n담당 부서는 기술지원입니다.",
              "name: {PERSON_EN}\nemail: {EMAIL}\nphone: {PHONE}",
              "문의하신 분은 {PERSON}입니다. 회신: {EMAIL}",
              "{PERSON}에게 보낼 문서입니다. 전화번호 {PHONE}로 확인 부탁합니다.",
              "배송 정보\n받는 분 = {PERSON}\n배송 주소 = {ADDRESS}\n연락처 = {PHONE}",
              "Hello {PERSON_EN}, please use {EMAIL} for support.",
              "Contact {PERSON_EN} at {ADDRESS_EN}. Phone {PHONE}",
              "연락 담당 {PERSON}, 메일 {EMAIL}, 번호 {PHONE}.",
              "{PERSON}의 신청서에 기재된 주소는 {ADDRESS}입니다.",
              "Full name = {PERSON_EN}\nPostal address = {ADDRESS_EN}\nTelephone = {PHONE}"),
    "validation": ("수신자: {PERSON}\n거주지: {ADDRESS}\n연락처: {PHONE}",
                   "확인 요청입니다. {PERSON}씨의 이메일은 {EMAIL}입니다.",
                   "recipient: {PERSON_EN}\naddress: {ADDRESS_EN}\nemail: {EMAIL}"),
    "test": ("고객: {PERSON}\n배송지: {ADDRESS}\n이메일: {EMAIL}",
             "제 이름은 {PERSON}이라고 합니다. 번호는 {PHONE}입니다.",
             "customer: {PERSON_EN}\naddress: {ADDRESS_EN}\nemail: {EMAIL}"),
}


def _generate_legacy(count: int, *, split: str = "train", seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        values = {"PERSON": rng.choice(NAMES), "ADDRESS": rng.choice(ADDRESSES),
                  "PHONE": f"010-{rng.randrange(1000,9999)}-{rng.randrange(1000,9999)}",
                  "EMAIL": f"user{rng.randrange(10000)}@example.com",
                  "IDENTIFIER": f"900101-{rng.randrange(1000000,1999999)}",
                  "PERSON_EN": rng.choice(("Alice Kim", "Daniel Park", "Emma Lee", "Noah Choi")),
                  "ADDRESS_EN": f"{rng.randrange(1,999)} Example Street, Test City"}
        family = index % len(FAMILIES[split])
        template = FAMILIES[split][family]
        if index % 7 == 0:
            text = rng.choice(("내일 회의는 오후 세 시입니다. 개인정보가 없는 문장입니다.",
                               "The release is ready. Please review the documentation.", "주소 형식과 전화번호 입력 방법을 설명해주세요."))
            spans = []
        else:
            text, spans = "", []
            import re
            cursor = 0
            for match in re.finditer(r"\{([A-Z_]+)\}", template):
                text += template[cursor:match.start()]
                kind = match.group(1)
                start = len(text)
                text += values[kind]
                spans.append({"start": start, "end": len(text), "type": kind.removesuffix("_EN")})
                cursor = match.end()
            text += template[cursor:]
        rows.append({"id": f"{split}-{index:05}", "family": f"{split}-{family}", "text": text, "spans": spans})
    return rows


# Pools and entire template families are disjoint between partitions. Values
# are fabricated for this benchmark, not collected from customer documents.
# Holding out names and addresses makes recognizing a semantic role necessary;
# memorizing the small legacy dictionaries cannot solve this recipe.
DIVERSE_POOLS = {
    "train": {
        "surnames": ("김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오"),
        "first_en": ("Alina", "Brandon", "Camila", "Derek", "Ethan", "Fiona", "Gavin", "Helena", "Ian", "Jasper"),
        "last_en": ("Foster", "Martin", "Clark", "Wright", "Young", "Scott"),
        "regions": ("서울특별시 마포구", "부산광역시 동래구", "대전광역시 서구", "경기도 성남시 분당구"),
        "roads": ("월드컵로", "충렬대로", "둔산로", "판교역로", "느티로", "동백길"),
        "streets_en": ("Alder Road", "Cedar Avenue", "Maple Lane", "Juniper Street"),
        "cities_en": ("Northport", "Eastfield", "Brookvale"),
        "email_domains": ("post.train.example", "team.training.example", "office.train.example"),
        "phone_group": 1000, "id_group": 0,
    },
    "validation": {
        "surnames": ("신", "서", "권", "황", "안", "송"),
        "first_en": ("Kendall", "Leona", "Mateo", "Nina", "Oscar", "Paula"),
        "last_en": ("Evans", "Hughes", "Carter", "Reed"),
        "regions": ("인천광역시 부평구", "광주광역시 북구", "세종특별자치시", "충청남도 천안시 서북구"),
        "roads": ("부평대로", "첨단과기로", "도담로", "불당로", "가람길"),
        "streets_en": ("Pine Crescent", "Birch Drive", "Willow Court"),
        "cities_en": ("Westhaven", "Clearwater", "Hillcrest"),
        "email_domains": ("mail.validation.example", "desk.valid.example", "staff.validation.example"),
        "phone_group": 4000, "id_group": 300000,
    },
    "test": {
        "surnames": ("홍", "유", "전", "고", "문", "양"),
        "first_en": ("Quentin", "Rosa", "Soren", "Trevor", "Yara", "Zachary"),
        "last_en": ("Bennett", "Cole", "Morris", "Hayes"),
        "regions": ("대구광역시 수성구", "울산광역시 남구", "강원특별자치도 춘천시", "전라남도 순천시"),
        "roads": ("달구벌대로", "삼산로", "중앙로", "연향로", "솔바람길"),
        "streets_en": ("Oak Terrace", "Elm Boulevard", "Aspen Way"),
        "cities_en": ("Southridge", "Lakewood", "Riverford"),
        "email_domains": ("reply.test.example", "contact.testing.example", "people.test.example"),
        "phone_group": 7000, "id_group": 600000,
    },
}
GIVEN_SYLLABLES = ("민", "준", "서", "지", "우", "수", "진", "은", "현", "도", "재", "하", "성", "연", "태", "희")

DIVERSE_FAMILIES = {
    "train": (
        ("ko", "상담 신청 내용입니다. 성명은 {PERSON}, 거주 주소는 {ADDRESS}, 전화는 {PHONE}, 이메일은 {EMAIL}, 개인 식별번호는 {IDENTIFIER}입니다."),
        ("en", "Private contact record: full name {PERSON_EN}; home address {ADDRESS_EN}; phone {PHONE}; email {EMAIL}; personal identifier {IDENTIFIER}."),
        ("mixed", "개인 연락 카드 / Name {PERSON_EN}; 주소 {ADDRESS}; mobile {PHONE}; e-mail {EMAIL}; ID {IDENTIFIER}."),
        ("ko", "신청한 사람은 {PERSON}입니다. 집으로 보낼 우편물 주소는 {ADDRESS}입니다."),
        ("ko", "상담을 맡은 {PERSON}에게 {PHONE}로 연락할 수 있습니다."),
        ("ko", "회신을 받을 사람: {PERSON}\n회신 이메일: {EMAIL}"),
        ("ko", "본인 확인에 사용할 식별번호는 {IDENTIFIER}이며 명의자는 {PERSON}입니다."),
        ("ko", "회원 {PERSON}의 거주지는 {ADDRESS}이고 연락처는 {PHONE}입니다."),
        ("ko", "우편물 받을 곳을 {ADDRESS}로 적었습니다. 수령인은 {PERSON}입니다."),
        ("ko", "{PERSON}의 계정에 연결된 이메일 주소를 {EMAIL}로 기록합니다."),
        ("ko", "제출자 {PERSON} / 휴대전화 {PHONE} / 개인번호 {IDENTIFIER}"),
        ("ko", "실명은 {PERSON}이며 자택은 {ADDRESS}입니다. 전자우편은 {EMAIL}입니다."),
        ("ko", "접수자의 성명은 {PERSON}입니다. 개인 확인 코드 {IDENTIFIER}를 등록했습니다."),
        ("ko", "연락 받을 휴대폰: {PHONE}\n이메일 계정: {EMAIL}"),
        ("ko", "개인의 등록번호 {IDENTIFIER}를 별도 보관합니다. 연락처는 {PHONE}입니다."),
        ("ko", "사는 곳: {ADDRESS}\n거주자 성명: {PERSON}"),
        ("ko", "{PERSON}님, 전화 {PHONE}와 메일 {EMAIL}을 확인해 주십시오."),
        ("ko", "성명 항목에 {PERSON}을 입력했습니다. 주소 항목은 {ADDRESS}입니다."),
        ("ko", "보호 대상 정보 — 이름 {PERSON}; 식별값 {IDENTIFIER}; 이메일 {EMAIL}."),
        ("ko", "문의한 {PERSON}씨에게 {EMAIL}로 답장을 보내고 {PHONE}로 안내합니다."),
        ("en", "The applicant is {PERSON_EN}. Their residential address is {ADDRESS_EN}."),
        ("en", "Send a private reply to {PERSON_EN} via {EMAIL}; their telephone is {PHONE}."),
        ("en", "Account owner {PERSON_EN} supplied personal ID {IDENTIFIER}."),
        ("en", "Residential delivery for {PERSON_EN}: {ADDRESS_EN}. Contact number: {PHONE}."),
        ("en", "Personal email account {EMAIL} belongs to {PERSON_EN}."),
        ("en", "For identity verification, record {IDENTIFIER} and the owner's name {PERSON_EN}."),
        ("en", "Home location: {ADDRESS_EN}\nResident: {PERSON_EN}\nPrivate email: {EMAIL}"),
        ("en", "Reach the resident on {PHONE}. Their personal ID is {IDENTIFIER}."),
        ("mixed", "신청자 name: {PERSON_EN}\n자택 address: {ADDRESS}\n회신 email: {EMAIL}"),
        ("mixed", "Contact person {PERSON}; 전화번호 {PHONE}; personal code {IDENTIFIER}."),
        ("mixed", "해외 우편을 받을 {PERSON_EN}의 주소는 {ADDRESS_EN}이며 이메일은 {EMAIL}입니다."),
        ("mixed", "본인 확인 / holder {PERSON_EN} / 개인번호 {IDENTIFIER} / phone {PHONE}"),
        ("ko", "성명: {PERSON}\n주민 식별값: {IDENTIFIER}\n연락용 전자우편: {EMAIL}"),
        ("ko", "개인 전화번호 {PHONE}를 적었습니다. 거주 주소는 {ADDRESS}입니다."),
        ("en", "Private mailing address {ADDRESS_EN}; personal contact mailbox {EMAIL}."),
        ("mixed", "회원 {PERSON} (personal identifier {IDENTIFIER}), email {EMAIL}."),
    ),
    "validation": (
        ("ko", "개인 정보 확인서에는 이름 {PERSON}, 거주지 {ADDRESS}, 연락 전화 {PHONE}, 메일 {EMAIL}, 본인번호 {IDENTIFIER}가 적혀 있습니다."),
        ("en", "This confidential profile lists {PERSON_EN} as the owner, {ADDRESS_EN} as the home, {PHONE} as the mobile, {EMAIL} as the mailbox and {IDENTIFIER} as the identity code."),
        ("mixed", "개인 profile — owner {PERSON_EN}; 주소지 {ADDRESS}; 전화 {PHONE}; email {EMAIL}; 식별번호 {IDENTIFIER}."),
        ("ko", "우편 수령자의 이름은 {PERSON}이고 수령 장소는 {ADDRESS}입니다."),
        ("ko", "담당 실명 {PERSON}의 회신용 메일은 {EMAIL}, 휴대폰 번호는 {PHONE}입니다."),
        ("ko", "계정의 주인은 {PERSON}이며 본인 확인 번호는 {IDENTIFIER}로 지정되어 있습니다."),
        ("ko", "자택 주소 항목에는 {ADDRESS}를, 개인 전화 항목에는 {PHONE}를 기재합니다."),
        ("en", "The named resident, {PERSON_EN}, receives private correspondence at {ADDRESS_EN}."),
        ("en", "For this person's account use email {EMAIL}, phone {PHONE}, and identity reference {IDENTIFIER}."),
        ("mixed", "한국 거주자 {PERSON}의 private mailbox는 {EMAIL}이며 identity code는 {IDENTIFIER}입니다."),
        ("en", "Identity details for {PERSON_EN}: residential location {ADDRESS_EN}; private contact {EMAIL}."),
        ("ko", "연락용 이메일 {EMAIL}과 개인 식별번호 {IDENTIFIER}를 별도 칸에 기록합니다."),
    ),
    "test": (
        ("ko", "보관된 개인 기록: 실명 {PERSON}; 자택 위치 {ADDRESS}; 휴대전화번호 {PHONE}; 전자우편 계정 {EMAIL}; 개인 식별 코드 {IDENTIFIER}."),
        ("en", "Confidential individual details — legal name {PERSON_EN}, residence {ADDRESS_EN}, mobile number {PHONE}, email account {EMAIL}, and identity value {IDENTIFIER}."),
        ("mixed", "본인 등록 내역 / legal name {PERSON_EN} / 집 주소 {ADDRESS} / mobile {PHONE} / 메일 {EMAIL} / personal ID {IDENTIFIER}"),
        ("ko", "개인이 우편을 받는 주소를 {ADDRESS}로 확인했습니다. 수령 실명은 {PERSON}입니다."),
        ("ko", "{PERSON}님이 남긴 휴대전화는 {PHONE}이고 개인 이메일은 {EMAIL}입니다."),
        ("ko", "신원을 확인할 때 성명 {PERSON}과 개인번호 {IDENTIFIER}를 함께 제시합니다."),
        ("ko", "거주 위치로 {ADDRESS}가 기록되어 있으며 회신용 휴대폰은 {PHONE}입니다."),
        ("en", "Private post should reach resident {PERSON_EN} at the home location {ADDRESS_EN}."),
        ("en", "Personal account metadata: mailbox {EMAIL}; telephone {PHONE}; identity marker {IDENTIFIER}."),
        ("mixed", "본인 실명 {PERSON} / private e-mail {EMAIL} / verification ID {IDENTIFIER}"),
        ("en", "The individual {PERSON_EN} provided home details {ADDRESS_EN} and a private mailbox {EMAIL}."),
        ("ko", "본인 확인 코드 {IDENTIFIER}를 저장했고 이메일 연락처는 {EMAIL}로 설정했습니다."),
    ),
}

DIVERSE_NEGATIVES = {
    "train": (
        ("ko", "이름 입력란은 비어 있고 주소 입력란도 아직 채우지 않았습니다. 안내 문서 {number}쪽을 살펴봅니다."),
        ("ko", "전화번호와 이메일의 형식을 설명하는 교육입니다. 예제 순서 {number}에는 실제 연락 정보가 없습니다."),
        ("ko", "성명: (미입력)\n거주 주소: (미입력)\n공개 도움말 항목: {number}"),
        ("en", "The name and address fields are empty. Public form revision {number} explains the layout."),
        ("en", "No personal phone or email has been supplied. Refer to public tutorial section {number}."),
        ("ko", "고객 이름을 수집하지 않는 화면을 검토합니다. 공개 메뉴 번호는 {number}입니다."),
        ("mixed", "Name field: empty / 주소: 입력 전 / public guide chapter {number}."),
        ("ko", "개인 식별번호의 예시를 넣지 않습니다. 공개 기능 설명서의 버전은 {number}입니다."),
        ("en", "Discuss address formatting without a real address. The public sample counter is {number}."),
        ("ko", "보낼 사람과 받을 사람을 모두 선택하지 않았습니다. 문서 검토 순서 {number}를 확인합니다."),
    ),
    "validation": (
        ("ko", "이 양식에는 성명이나 주소가 기재되지 않았습니다. 공개 지침 페이지 {number}를 참고하십시오."),
        ("en", "Both customer name and residential address are absent; consult public instruction item {number}."),
        ("ko", "휴대전화 칸과 전자우편 칸은 빈칸으로 남겨 두었습니다. 메뉴 설명은 공개 항목 {number}에 있습니다."),
        ("mixed", "Customer details: omitted / 개인 정보: 없음 / public manual entry {number}."),
        ("ko", "본인 확인 값은 입력 전입니다. 공개 소프트웨어 개정판 {number}의 도움말을 읽습니다."),
        ("en", "A lesson on personal identifiers includes no identifier values. Public lesson index: {number}."),
    ),
    "test": (
        ("ko", "개인의 이름 및 집 주소를 표시하지 않는 빈 서식입니다. 공개 서식 설명 순번 {number}를 읽어 주세요."),
        ("en", "The customer contact form contains no personal details; its public help entry is {number}."),
        ("ko", "연락 번호: (제공되지 않음)\n메일 계정: (제공되지 않음)\n공개 안내 절: {number}"),
        ("mixed", "Personal name: not provided / 집 주소: 비공개 입력 없음 / public lesson {number}."),
        ("ko", "식별 코드 항목은 아직 비어 있습니다. 공개 응용프로그램 메뉴 {number}를 살펴봅니다."),
        ("en", "Name, phone, and email boxes remain blank. Public layout illustration number {number} has no contact values."),
    ),
}


def _render(template: str, values: dict[str, str]) -> tuple[str, list[dict]]:
    text, spans, cursor = "", [], 0
    for match in re.finditer(r"\{([A-Z_]+)\}", template):
        text += template[cursor:match.start()]
        key = match.group(1)
        start = len(text)
        text += values[key]
        spans.append({"start": start, "end": len(text), "type": key.removesuffix("_EN")})
        cursor = match.end()
    return text + template[cursor:], spans


def _diverse_values(rng: random.Random, split: str, index: int, seed: int) -> dict[str, str]:
    pools = DIVERSE_POOLS[split]
    person = rng.choice(pools["surnames"]) + rng.choice(GIVEN_SYLLABLES) + rng.choice(GIVEN_SYLLABLES)
    person_en = rng.choice(pools["first_en"]) + " " + rng.choice(pools["last_en"])
    region, road = rng.choice(pools["regions"]), rng.choice(pools["roads"])
    number = rng.randrange(1, 500)
    address = f"{region} {road} {number}"
    if index % 3 == 0:
        address += f" {rng.randrange(1, 16)}층"
    elif index % 3 == 1:
        address += f" {rng.randrange(101, 1601)}호"
    address_en = f"{number} {rng.choice(pools['streets_en'])}, {rng.choice(pools['cities_en'])}"
    # Numeric blocks guarantee disjoint normalized phone and RRN-like values.
    # These are fabricated syntactic fixtures, never asserted to be valid IDs.
    sequence = (index + abs(seed) * 7919) % 3_000_000
    phone_digits = f"010{pools['phone_group'] + sequence // 10000:04}{sequence % 10000:04}"
    phone = (f"{phone_digits[:3]}-{phone_digits[3:7]}-{phone_digits[7:]}" if index % 4 != 1
             else f"{phone_digits[:3]} {phone_digits[3:7]} {phone_digits[7:]}")
    birth = f"{rng.randrange(70, 100):02}{rng.randrange(1, 13):02}{rng.randrange(1, 29):02}"
    identifier = f"{birth}-1{pools['id_group'] + sequence % 300000:06}"
    email = f"contact{index:06}.s{abs(seed) % 10000}@{rng.choice(pools['email_domains'])}"
    return {"PERSON": person, "PERSON_EN": person_en, "ADDRESS": address,
            "ADDRESS_EN": address_en, "PHONE": phone, "EMAIL": email, "IDENTIFIER": identifier}


def _generate_diverse(count: int, *, split: str, seed: int) -> list[dict]:
    rng = random.Random(f"diverse-v1:{split}:{seed}")
    rows, positive, negative = [], 0, 0
    for index in range(count):
        if index % 5 == 0:
            family = negative % len(DIVERSE_NEGATIVES[split])
            language, template = DIVERSE_NEGATIVES[split][family]
            text = template.format(number=index + abs(seed) * 100000)
            spans = []
            template_id = f"diverse-v1:{split}:negative:{family:02}"
            negative += 1
        else:
            family = positive % len(DIVERSE_FAMILIES[split])
            language, template = DIVERSE_FAMILIES[split][family]
            text, spans = _render(template, _diverse_values(rng, split, index, seed))
            template_id = f"diverse-v1:{split}:positive:{family:02}"
            positive += 1
        if len(text) > 256:
            raise ValueError(f"diverse-v1 paragraph exceeds character budget: {template_id}")
        rows.append({"id": f"{split}-{index:05}", "recipe": "diverse-v1", "family": template_id,
                     "template_id": template_id, "language": language, "text": text, "spans": spans})
    return rows


def generate(count: int, *, split: str = "train", seed: int = 42, recipe: str = "legacy") -> list[dict]:
    """Generate an isolated partition; legacy is unchanged for replayability."""
    if type(count) is not int or count < 0 or split not in FAMILIES or recipe not in RECIPES:
        raise ValueError("invalid synthetic dataset configuration")
    if type(seed) is not int:
        raise ValueError("dataset seed must be an integer")
    if recipe == "legacy":
        return _generate_legacy(count, split=split, seed=seed)
    return _generate_diverse(count, split=split, seed=seed)


def _jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")


def dataset_manifest(partitions: dict[str, list[dict]], *, recipe: str, seed: int) -> dict:
    """Record exact file hashes and disclose leakage in every recipe."""
    splits = {}
    text_sets, value_sets, template_sets = {}, {}, {}
    for split, rows in partitions.items():
        counts = Counter(span["type"] for row in rows for span in row["spans"])
        text_sets[split] = {row["text"] for row in rows}
        value_sets[split] = {kind: {row["text"][span["start"]:span["end"]] for row in rows
                                  for span in row["spans"] if span["type"] == kind}
                             for kind in ("PERSON", "ADDRESS", "PHONE", "EMAIL", "IDENTIFIER")}
        template_sets[split] = {row["family"] for row in rows}
        splits[split] = {"count": len(rows), "sha256": hashlib.sha256(_jsonl(rows)).hexdigest(),
                         "max_characters": max((len(row["text"]) for row in rows), default=0),
                         "negative_count": sum(not row["spans"] for row in rows),
                         "entity_counts": dict(sorted(counts.items())),
                         "template_ids": sorted(template_sets[split]),
                         "unique_texts": len(text_sets[split])}
    overlaps = {}
    names = tuple(partitions)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            overlaps[f"{left}:{right}"] = {"exact_texts": len(text_sets[left] & text_sets[right]),
                                           "template_ids": len(template_sets[left] & template_sets[right]),
                                           "entity_values": {kind: len(value_sets[left][kind] & value_sets[right][kind])
                                                             for kind in value_sets[left]}}
    definition = {"recipe": recipe, "families": FAMILIES if recipe == "legacy" else DIVERSE_FAMILIES}
    if recipe == "diverse-v1":
        definition.update(pools=DIVERSE_POOLS, negatives=DIVERSE_NEGATIVES,
                          given_syllables=GIVEN_SYLLABLES, value_generator_version=1)
    return {"format": "ganglion-synthetic-dataset-v1", "recipe": recipe, "seed": seed,
            "recipe_sha256": hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "provenance": "fabricated synthetic paragraphs; not a real-document quality benchmark",
            "splits": splits, "cross_split_overlap": overlaps}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate grouped synthetic PII data")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--recipe", choices=RECIPES, default="legacy")
    args = parser.parse_args(argv)
    if args.count < 0:
        parser.error("--count must be nonnegative")
    args.output.mkdir(parents=True, exist_ok=True)
    partitions = {}
    for split, count in (("train", args.count), ("validation", max(20, args.count // 4)), ("test", max(20, args.count // 4))):
        rows = generate(count, split=split, seed=args.seed, recipe=args.recipe)
        partitions[split] = rows
        (args.output / f"{split}.jsonl").write_bytes(_jsonl(rows))
    manifest = dataset_manifest(partitions, recipe=args.recipe, seed=args.seed)
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "train": args.count, "recipe": args.recipe, "seed": args.seed,
                      "max_characters": {split: data["max_characters"] for split, data in manifest["splits"].items()}}))


if __name__ == "__main__":
    main()
