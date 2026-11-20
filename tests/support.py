import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from closeout.jobs import close_batch
from closeout.results import initialize_closeout
from dispatch.envelope import ReportEnvelope
from dispatch.issuance import issue_report
from dispatch.outbox import initialize_outbox
from intake.batches import accept_batch
from intake.outlets import register_outlet
from intake.source_store import initialize_intake, open_source
from posting.ledger import post_report
from posting.receiver_store import initialize_receiver, open_receiver
from posting.staging import stage_report


PLANT = '河湾'
DAY = '2026-11-18'


def readings(*pairs):
    return [{'outlet': outlet, 'ml': ml} for outlet, ml in pairs]


def rewrap(envelope, body):
    return ReportEnvelope(envelope.report_id, hashlib.sha256(body).hexdigest(), body)


def edit_body(envelope, edit):
    payload = json.loads(envelope.body)
    edit(payload)
    return rewrap(envelope, json.dumps(payload, ensure_ascii=False).encode('utf-8'))


class LocalDelivery:
    def __init__(self):
        self.request = None
        self.reply = None

    def send(self, receiver, envelope, *, hold_request=False, hold_reply=False):
        if hold_request:
            if self.request is not None:
                raise ValueError('a request is already held')
            self.request = envelope
            return None
        if hold_reply and self.reply is not None:
            raise ValueError('a reply is already held')
        stage_id = stage_report(receiver, envelope)
        reply = post_report(receiver, stage_id).receipt.to_payload()
        if hold_reply:
            self.reply = reply
            return None
        return reply

    def release_request(self, receiver, *, hold_reply=False):
        if self.request is None:
            raise ValueError('no held request')
        reply = self.send(receiver, self.request, hold_reply=hold_reply)
        self.request = None
        return reply

    def release_reply(self):
        if self.reply is None:
            raise ValueError('no held reply')
        reply, self.reply = self.reply, None
        return reply


class CommitFailureConnection(sqlite3.Connection):
    def arm(self, observe):
        self.execute('CREATE TEMP TABLE IF NOT EXISTS commit_parent (id INTEGER PRIMARY KEY)')
        self.execute('''CREATE TEMP TABLE IF NOT EXISTS commit_guard (
            parent_id INTEGER REFERENCES commit_parent(id) DEFERRABLE INITIALLY DEFERRED
        )''')
        self._commit_observer = observe

    def commit(self):
        observe = getattr(self, '_commit_observer', None)
        if observe is not None:
            self._commit_observer = None
            observe(self)
            self.execute('INSERT INTO temp.commit_guard (parent_id) VALUES (1)')
        super().commit()


class WarehouseCase(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(TemporaryDirectory()))
        self.source_path = folder / 'source.sqlite3'
        self.receiver_path = folder / 'receiver.sqlite3'
        self.source = open_source(self.source_path)
        self.receiver = open_receiver(self.receiver_path)
        self.addCleanup(self.source.close)
        self.addCleanup(self.receiver.close)
        self.initialize_source()
        initialize_receiver(self.receiver)
        register_outlet(self.source, PLANT, 'A')
        register_outlet(self.source, PLANT, 'B')

    def initialize_source(self):
        initialize_intake(self.source)
        initialize_closeout(self.source)
        initialize_outbox(self.source)

    def reopen_source(self):
        self.source.close()
        self.source = open_source(self.source_path)
        self.addCleanup(self.source.close)
        return self.source

    def reopen_receiver(self, *, factory=sqlite3.Connection):
        self.receiver.close()
        self.receiver = open_receiver(self.receiver_path, factory=factory)
        self.addCleanup(self.receiver.close)
        return self.receiver

    def accept(self, rows, *, batch='daily', parent_id=None):
        return accept_batch(self.source, PLANT, DAY, batch, rows, parent_id=parent_id)

    def issue(self, rows=None, *, report_id='R7'):
        if rows is None:
            rows = readings(('A', 5), ('A', 7))
        revision_id = self.accept(rows, batch=report_id)
        result = close_batch(self.source, revision_id)
        return issue_report(self.source, report_id, result.result_id)

    def stage_and_post(self, issued):
        stage_id = stage_report(self.receiver, issued.envelope)
        return post_report(self.receiver, stage_id)
