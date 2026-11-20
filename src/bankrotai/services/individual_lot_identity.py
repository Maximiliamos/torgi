"""Known legacy procedure identifiers do not identify an individual lot."""
import re


def unscoped_procedure_identity(source_system, external_id):
    if source_system not in {"sistematorg.com", "aukcioncenter.ru"} or not isinstance(external_id, str):
        return False
    return bool(re.fullmatch(re.escape(source_system)+r":\d+", external_id)
                or re.fullmatch(r"\d+", external_id))
