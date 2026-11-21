"""Step 1: Load the official OFAC SDN list (SDN_ENHANCED.XML) and extract
digital currency addresses with each entity's programs and legal authorities.

Standard library only. Usage:
    python -I app/sdn_loader.py <path/to/SDN_ENHANCED.XML> <out_dir>
"""
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

NS = "{https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ENHANCED_XML}"
EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
DCA_PREFIX = "Digital Currency Address - "


def _text(el, path):
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(xml_path: Path):
    data_as_of = None
    wallets = []
    entities_with_wallets = 0

    for event, el in ET.iterparse(xml_path, events=("end",)):
        tag = el.tag.replace(NS, "")
        if tag == "dataAsOf" and data_as_of is None:
            data_as_of = el.text
        if tag != "entity":
            continue

        features = el.findall(f"{NS}features/{NS}feature")
        dcas = []
        for f in features:
            ftype = _text(f, f"{NS}type") or ""
            if ftype.startswith(DCA_PREFIX):
                dcas.append((ftype[len(DCA_PREFIX):], _text(f, f"{NS}value")))

        if dcas:
            entities_with_wallets += 1
            primary_name = None
            for name in el.findall(f"{NS}names/{NS}name"):
                if _text(name, f"{NS}isPrimary") == "true":
                    primary_name = _text(
                        name, f"{NS}translations/{NS}translation/{NS}formattedFullName"
                    )
                    break
            programs = [p.text for p in el.findall(f"{NS}sanctionsPrograms/{NS}sanctionsProgram")]
            authorities = [a.text for a in el.findall(f"{NS}legalAuthorities/{NS}legalAuthority")]
            countries = sorted({c.text for c in el.findall(f"{NS}addresses/{NS}address/{NS}country") if c.text})
            entity_type = _text(el, f"{NS}generalInfo/{NS}entityType")
            for currency, address in dcas:
                if not address:
                    continue
                wallets.append({
                    "address": address,
                    "address_lower": address.lower(),
                    "currency": currency,
                    "evm": bool(EVM_RE.match(address)),
                    "entity_id": el.get("id"),
                    "entity_name": primary_name,
                    "entity_type": entity_type,
                    "programs": programs,
                    "legal_authorities": authorities,
                    "countries": countries,
                })
        el.clear()

    return data_as_of, wallets, entities_with_wallets


def main():
    xml_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    data_as_of, wallets, n_entities = load(xml_path)
    version = {
        "source_file": xml_path.name,
        "sha256": sha256_of(xml_path),
        "data_as_of": data_as_of,
        "loaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entities_with_wallets": n_entities,
        "wallet_count": len(wallets),
        "evm_wallet_count": sum(w["evm"] for w in wallets),
    }
    (out_dir / "list_version.json").write_text(json.dumps(version, indent=2))
    (out_dir / "sdn_wallets.json").write_text(json.dumps(wallets, indent=2, ensure_ascii=False))
    print(json.dumps(version, indent=2))


if __name__ == "__main__":
    main()
