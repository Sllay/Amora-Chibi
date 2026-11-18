"""Run 3: no-network offline PCS.4C v18 preflight and redaction gates."""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3
import zipfile

import pytest

from amora.storage import postgres_run3_snapshot_preflight as preflight
from amora.storage.postgres_v18_manifest import synthetic_v18_connection


@pytest.fixture
def fabricated_pair(tmp_path):
    path=tmp_path/"private-fixture.db"
    with closing(synthetic_v18_connection()) as source:
        with closing(sqlite3.connect(path)) as target:
            source.backup(target)
    with zipfile.ZipFile(str(path)+".avatar.zip","w") as archive:
        archive.writestr("manifest.json",json.dumps({
            "version":1,"assets":[],
        }))
    return path


def test_verified_v18_pair_never_certifies_aiven_or_exposes_private_details(
    fabricated_pair,capsys,
):
    report=preflight.inspect_private_snapshot(fabricated_pair)
    assert report["status"]=="verified_offline"
    assert report["schema_version"]==18
    assert report["logical_tables"]==51
    assert report["snapshot_bytes"]>0
    assert report["sidecar_bytes"]>0
    assert report["production_cutover_authorized"] is False
    assert report["source_freshness"]=="not_certified"
    assert report["aiven_restore"]=="not_tested"
    assert report["aiven_capacity"]=="not_measured"
    assert not any("hash" in key or "path" in key for key in report)
    assert preflight.main([
        "--snapshot",str(fabricated_pair),"--confirm-offline",
    ])==0
    output=capsys.readouterr().out
    assert str(fabricated_pair) not in output
    assert "sha256" not in output
    assert json.loads(output)["production_cutover_authorized"] is False


def test_operator_confirmation_required_before_reading_private_file(
    fabricated_pair,monkeypatch,capsys,
):
    def forbidden(*args,**kwargs):
        raise AssertionError("PRIVATE_SOURCE_READ_WITHOUT_OPERATOR_CONFIRMATION")
    monkeypatch.setattr(preflight,"inspect_private_snapshot",forbidden)
    assert preflight.main(["--snapshot",str(fabricated_pair)])==2
    assert "RUN3_OPERATOR_CONFIRM_OFFLINE_REQUIRED" in capsys.readouterr().out


def test_drift_and_tampering_are_rejected_with_sanitized_errors(
    fabricated_pair,capsys,
):
    with sqlite3.connect(fabricated_pair) as source:
        source.execute("CREATE TABLE unexpected_private_table (secret TEXT)")
        source.execute("INSERT INTO unexpected_private_table VALUES ('secret-message')")
    with pytest.raises(preflight.Run3SnapshotError,match="RUN3_SNAPSHOT_V18_DDL_DRIFT"):
        preflight.inspect_private_snapshot(fabricated_pair)
    assert preflight.main([
        "--snapshot",str(fabricated_pair),"--confirm-offline",
    ])==1
    output=capsys.readouterr().out
    assert "secret-message" not in output
    assert str(fabricated_pair) not in output
    assert json.loads(output)["production_cutover_authorized"] is False


def test_missing_sidecar_symlink_and_bad_archive_fail_closed(
    fabricated_pair,tmp_path,capsys,
):
    sidecar=str(fabricated_pair)+".avatar.zip"
    with zipfile.ZipFile(sidecar,"w") as archive:
        archive.writestr("manifest.json",json.dumps({
            "version":1,"assets":[{"id":"private-data"}],
        }))
    assert preflight.main([
        "--snapshot",str(fabricated_pair),"--confirm-offline",
    ])==1
    assert "RUN3_SNAPSHOT_VERIFICATION_FAILED" in capsys.readouterr().out
    symlink=tmp_path/"linked-private.db"
    symlink.symlink_to(fabricated_pair)
    with pytest.raises(preflight.Run3SnapshotError,match="RUN3_SNAPSHOT_PRIVATE_PAIR_REQUIRED"):
        preflight.inspect_private_snapshot(symlink)


def test_source_change_during_audit_rejected(fabricated_pair,monkeypatch):
    real_sha=preflight._sha
    calls=0
    def swapped(path):
        nonlocal calls
        calls+=1
        if calls==3:
            return "0"*64
        return real_sha(path)
    monkeypatch.setattr(preflight,"_sha",swapped)
    with pytest.raises(preflight.Run3SnapshotError,match="RUN3_SNAPSHOT_CHANGED_DURING_AUDIT"):
        preflight.inspect_private_snapshot(fabricated_pair)
