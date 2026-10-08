"""Pure normalization of cadastral provider property metadata (BAT-308).

Converts NSPD/legacy PKK source property aliases into stable Russian field keys.
No requests, DB sessions, private state or application settings.
"""

from __future__ import annotations

import re
from typing import Any

CADASTRAL_RE = re.compile(r"^\d{2}:\d{2}:\d{6,7}:\d+$")


def normalize_nspd_props(props: dict, cadastral_number: str) -> dict[str, Any]:
    options = props.get("options") if isinstance(props.get("options"), dict) else {}

    def pick(*keys):
        for source in (options, props):
            for key in keys:
                val = source.get(key) if isinstance(source, dict) else None
                if val not in (None, ""):
                    return val
        return None

    observed_number = pick(
        "cad_num",
        "cadNum",
        "cadastralNumber",
        "cadastral_number",
        "cn",
        "label",
        "descr",
    )
    observed_number = str(observed_number or "").replace(" ", "")
    if not CADASTRAL_RE.match(observed_number):
        observed_number = cadastral_number or None

    object_name = pick("name", "objectName", "object_name")
    if object_name is not None and observed_number and str(object_name).replace(" ", "") == observed_number:
        object_name = None

    object_type = pick(
        "categoryName",
        "category_name",
        "objectType",
        "typeName",
        "type",
        "land_record_type",
        "build_record_type",
        "construction_record_type",
    )

    return {
        "Вид объекта недвижимости": object_type,
        "Дата присвоения": pick(
            "date_create",
            "assignDate",
            "assign_date",
            "cadRecordDate",
            "cad_record_date",
            "registration_date",
        ),
        "Кадастровый номер": observed_number,
        "Кадастровый квартал": pick(
            "quarter",
            "cadQuarter",
            "cad_quarter",
            "kvartal",
            "quarter_cad_number",
        ),
        "Адрес": pick(
            "readable_address",
            "readableAddress",
            "address",
            "object_address",
            "address_note",
            "location",
            "addr",
        ),
        "Наименование": object_name,
        "Назначение": pick(
            "purpose",
            "util_by_doc",
            "assignation",
            "building_purpose",
            "purpose_name",
        ),
        "Площадь общая": pick(
            "specified_area",
            "declared_area",
            "area",
            "area_value",
            "readableArea",
            "readable_area",
        ),
        "Статус": pick(
            "status",
            "state",
            "readableStatus",
            "readable_status",
            "cadRecordStatus",
            "cad_record_status",
        ),
        "Форма собственности": pick("ownership", "ownershipType", "ownership_type", "right_type", "fp"),
        "Кадастровая стоимость": pick(
            "cost_value",
            "cad_cost",
            "cadCost",
            "cost",
            "readableCadCost",
            "readable_cad_cost",
        ),
        "Удельный показатель кадастровой стоимости": pick(
            "cost_value_per_square_meter",
            "ud_cost",
            "unitCost",
            "unit_cost",
        ),
        "Количество этажей": pick("floors", "floorCount", "floor_count"),
        "Количество подземных этажей": pick(
            "undergroundFloors",
            "underground_floors",
            "underground_floor_count",
        ),
        "Материал стен": pick("wallMaterial", "wall_material"),
        "Завершение строительства": pick("yearBuilt", "year_built", "buildYear", "build_year"),
        "Ввод в эксплуатацию": pick(
            "commissioningYear",
            "commissioning_year",
            "year_commissioning",
        ),
        "ОКН": pick("culturalHeritage", "cultural_heritage", "heritage", "oks_flag"),
        "Без координат границ": pick("no_coords"),
        "Категория НСПД": pick("category", "categoryId", "category_id"),
    }


def normalize_pkk_attrs(attrs: dict, cadastral_number: str, kind: str) -> dict[str, Any]:
    def pick(*keys):
        for key in keys:
            val = attrs.get(key)
            if val not in (None, ""):
                return val
        return None

    object_type = "Здание" if kind == "building" else "Земельный участок"

    return {
        "Вид объекта недвижимости": pick("type_name", "type", "type_value", "obj_type") or object_type,
        "Дата присвоения": pick("date_create", "assign_date", "cad_record_date"),
        "Кадастровый номер": pick("cn", "cadnum", "cadastral_number") or cadastral_number,
        "Кадастровый квартал": pick("kvartal", "cad_quarter", "quarter"),
        "Адрес": pick("address", "addr", "address_note", "location"),
        "Наименование": pick("name", "object_name"),
        "Назначение": pick("util_by_doc", "purpose", "assignation"),
        "Площадь общая": pick("area_value", "area", "s"),
        "Единица площади": pick("area_unit", "area_type"),
        "Статус": pick("cad_record_status", "statecd", "state", "status"),
        "Форма собственности": pick("fp", "ownership", "right_type"),
        "Кадастровая стоимость": pick("cad_cost", "cad_cost_value", "cad_price"),
        "Удельный показатель кадастровой стоимости": pick("ud_cost", "unit_cost"),
        "Количество этажей": pick("floors", "floor_count"),
        "Количество подземных этажей": pick("underground_floors", "underground_floor_count"),
        "Завершение строительства": pick("year_built", "build_year"),
        "Ввод в эксплуатацию": pick("year_commissioning", "year_commisioning", "commissioning_year"),
        "ОКН": pick("cultural_heritage", "heritage", "oks_flag"),
    }
