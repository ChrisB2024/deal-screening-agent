from sqlalchemy.orm.properties import MappedColumn

from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


def test_uuid_primary_key_mixin_uses_uuid_primary_key():
    column = UUIDPrimaryKeyMixin.__dict__["id"]

    assert isinstance(column, MappedColumn)
    assert column.column.primary_key is True
    assert column.column.default is not None
    assert callable(column.column.default.arg)


def test_timestamp_mixin_columns_are_timezone_aware_and_non_nullable():
    created_at = TimestampMixin.__dict__["created_at"]
    updated_at = TimestampMixin.__dict__["updated_at"]

    assert isinstance(created_at, MappedColumn)
    assert isinstance(updated_at, MappedColumn)
    assert created_at.column.type.timezone is True
    assert updated_at.column.type.timezone is True
    assert created_at.column.nullable is False
    assert updated_at.column.nullable is False
    assert created_at.column.server_default is not None
    assert updated_at.column.server_default is not None
