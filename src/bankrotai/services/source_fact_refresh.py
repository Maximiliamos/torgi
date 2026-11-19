"""Keep source facts current independently of a user's investment decision."""

SOURCE_FACT_FIELDS = frozenset({
    "title", "description", "category", "region_slug", "region_name", "region_code",
    "address", "cadastral_number", "cadastral_numbers", "vin", "area", "object_name",
    "property_type", "total_area_gba", "gla", "land_area", "floors", "year_built",
    "legal_status", "encumbrances", "technical_condition", "power_kw", "parking_spaces",
    "lot_url", "source_url",
})

MANUAL_FINANCIAL_FIELDS = frozenset({
    "occupancy_rate", "anchor_tenants", "monthly_fixed_rent", "monthly_variable_rent",
    "monthly_other_income", "monthly_opex", "noi_annual", "land_risk_flag",
})

GEO_INPUT_FIELDS = ("address", "cadastral_number", "cadastral_numbers", "title",
                    "description", "region_name", "region_code")
