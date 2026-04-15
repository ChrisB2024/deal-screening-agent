from app.models.criteria import CriteriaConfig
from app.models.deal import DealAuditLog, Deal, DealDecision, DealScore, ExtractedField


def test_expected_tables_exist_in_metadata():
    table_names = set(Deal.metadata.tables)

    assert {
        "deals",
        "extracted_fields",
        "deal_scores",
        "deal_decisions",
        "deal_audit_log",
        "criteria_configs",
        "criteria",
    }.issubset(table_names)


def test_deal_relationships_use_delete_cascade():
    assert Deal.extracted_fields.property.cascade.delete_orphan
    assert Deal.scores.property.cascade.delete_orphan
    assert Deal.decisions.property.cascade.delete_orphan
    assert Deal.audit_logs.property.cascade.delete_orphan


def test_foreign_keys_match_builder_design():
    extracted_field_fk = next(iter(ExtractedField.__table__.c.deal_id.foreign_keys))
    decision_score_fk = next(iter(DealDecision.__table__.c.score_id.foreign_keys))
    score_config_fk = next(iter(DealScore.__table__.c.criteria_config_id.foreign_keys))
    audit_log_fk = next(iter(DealAuditLog.__table__.c.deal_id.foreign_keys))

    assert extracted_field_fk.ondelete == "CASCADE"
    assert decision_score_fk.ondelete == "SET NULL"
    assert score_config_fk.ondelete == "SET NULL"
    assert audit_log_fk.ondelete == "CASCADE"


def test_content_hash_uniqueness_is_tenant_scoped_to_preserve_isolation():
    unique_columns = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in Deal.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert tuple(sorted(("tenant_id", "content_hash"))) in unique_columns
    assert ("content_hash",) not in unique_columns


def test_criteria_config_versioning_is_enforced_per_tenant():
    unique_columns = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in CriteriaConfig.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert tuple(sorted(("tenant_id", "version"))) in unique_columns
