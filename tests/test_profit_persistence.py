"""Poda só depois de provas persistidas; sem rede ou carteiras reais."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from wallets.models import dumps, iso
from wallets.profit_persistence import (
    ensure_schema, latest_run, load_receipts, save_receipts_and_prune, save_run,
)
from wallets.storage import Store, _pack_payload, _unpack_payload

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
START = NOW-timedelta(days=7)
WALLET, OTHER = 'A'*44, 'B'*44


def raw(signature='sig', at=None):
    return {'slot':100, 'transactionIndex':2, 'blockTime':int((at or NOW-timedelta(hours=1)).timestamp()),
            'transaction':{'signatures':[signature], 'message':{'accountKeys':[WALLET], 'instructions':[]}},
            'meta':{'err':None,'fee':5000,'preBalances':[10000],'postBalances':[5000]}}


def receipt(transaction=None, **changes):
    transaction=transaction or raw()
    value={'schema':'sol-receipt-v1','wallet':WALLET,
           'signature':transaction['transaction']['signatures'][0],
           'timestamp':datetime.fromtimestamp(transaction['blockTime'],timezone.utc),
           'raw_hash':sha256(dumps(transaction).encode()).hexdigest(),
           'slot':transaction['slot'],'transaction_index':transaction.get('transactionIndex'),
           'programs':[],'status':'unknown','fee_sol':Decimal('0.000005'),
           'fee_attributed_to_wallet':True,'legs':[],
           'classification_complete':False,'sol_quote_verified':False,
           'components':{},'reasons':['unsupported_program']}
    value.update(changes)
    return value


class ProfitPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'profit.sqlite'
        self.store=Store(self.path);self.addCleanup(lambda:self.store.close())
        block=patch('socket.socket.connect',side_effect=AssertionError('Rede proibida'))
        block.start();self.addCleanup(block.stop)

    def save(self, receipts, transactions, **kwargs):
        return save_receipts_and_prune(self.store,WALLET,receipts,transactions,START,NOW,**kwargs)

    def count_raw(self, wallet=WALLET):
        return self.store.db.execute("SELECT COUNT(*) FROM source_transactions WHERE source='helius' AND address=?",(wallet,)).fetchone()[0]

    def test_unknown_receipt_persisted_compressed_before_raw_prune_and_replay_after_reopen(self):
        transaction=raw();self.store.put_source_transactions('helius',WALLET,[transaction])
        result=self.save([receipt(transaction)],[transaction])
        self.assertEqual((result['receipts_saved'],result['raw_pruned']),(1,1))
        self.assertEqual(self.count_raw(),0)
        saved=self.store.db.execute('SELECT payload FROM profit_receipts').fetchone()[0]
        self.assertIsInstance(saved,bytes)
        self.assertEqual(json.loads(_unpack_payload(saved))['fee_sol'],'0.000005')
        self.store.close();self.store=Store(self.path)
        loaded=load_receipts(self.store,WALLET,START,NOW)
        self.assertEqual(len(loaded),1)
        self.assertEqual(loaded[0]['status'],'unknown')
        self.assertFalse(loaded[0]['sol_quote_verified'])
        self.assertEqual(loaded[0]['legs'],[])
        self.assertEqual(loaded[0]['timestamp'],iso(NOW-timedelta(hours=1)))

    def test_repeat_is_idempotent_and_normalized_json_decimals_keep_the_same_hash(self):
        transaction=raw();one=receipt(transaction)
        self.store.put_source_transactions('helius',WALLET,[transaction])
        first=self.save([one,one],[transaction,transaction])
        decoded=json.loads(dumps(one))
        second=self.save([decoded],[transaction])
        self.assertEqual(second['manifest_id'],first['manifest_id'])
        self.assertEqual((second['receipts_saved'],second['raw_pruned']),(0,0))
        self.assertEqual(len(load_receipts(self.store,WALLET,START,NOW)),1)

    def test_receipt_missing_for_one_raw_preserves_every_raw_and_does_not_save_partial_proofs(self):
        a,b=raw('a'),raw('b')
        self.store.put_source_transactions('helius',WALLET,[a,b])
        with self.assertRaisesRegex(ValueError,'Falta recibo'):
            self.save([receipt(a)],[a,b])
        self.assertEqual(self.count_raw(),2)
        ensure_schema(self.store)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM profit_receipts').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM profit_receipt_manifests').fetchone()[0],0)

    def test_hash_mismatch_or_wrong_wallet_never_prunes_raw(self):
        transaction=raw();self.store.put_source_transactions('helius',WALLET,[transaction])
        for changes in ({'raw_hash':'0'*64},{'wallet':OTHER},{'raw_hash':'invalid'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.save([receipt(transaction,**changes)],[transaction])
            self.assertEqual(self.count_raw(),1)

    def test_existing_receipt_conflict_rolls_back_other_new_receipt_and_raw_deletes(self):
        a,b=raw('a'),raw('b')
        self.save([receipt(a)],[a])
        self.store.put_source_transactions('helius',WALLET,[a,b])
        conflicting=receipt(a,reasons=['different_interpretation'])
        with self.assertRaisesRegex(ValueError,'Recibo existente diverge'):
            self.save([receipt(b),conflicting],[a,b])
        self.assertEqual(self.count_raw(),2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM profit_receipts').fetchone()[0],1)
        self.assertEqual(len(load_receipts(self.store,WALLET,START,NOW)),1)

    def test_conflict_with_persisted_raw_rolls_back_all_new_receipts(self):
        transaction=raw();changed=deepcopy(transaction);changed['meta']['fee']=6000
        self.store.put_source_transactions('helius',WALLET,[changed])
        with self.assertRaisesRegex(ValueError,'Origem bruta guardada diverge'):
            self.save([receipt(transaction)],[transaction])
        self.assertEqual(self.count_raw(),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM profit_receipts').fetchone()[0],0)

    def test_duplicate_divergent_input_raw_or_receipt_is_rejected(self):
        transaction=raw();changed=deepcopy(transaction);changed['meta']['fee']=6000
        self.store.put_source_transactions('helius',WALLET,[transaction])
        with self.assertRaisesRegex(ValueError,'brutas repetidas'):
            self.save([receipt(transaction)],[transaction,changed])
        with self.assertRaisesRegex(ValueError,'Recibos repetidos'):
            self.save([receipt(transaction),receipt(transaction,reasons=['changed'])],[transaction])
        self.assertEqual(self.count_raw(),1)

    def test_no_other_wallet_or_unselected_signature_is_pruned(self):
        transaction=raw();untouched=raw('not-normalized')
        self.store.put_source_transactions('helius',WALLET,[transaction,untouched])
        self.store.put_source_transactions('helius',OTHER,[transaction])
        self.save([receipt(transaction)],[transaction])
        self.assertEqual(self.count_raw(),1)
        self.assertEqual(self.count_raw(OTHER),1)
        self.assertEqual(self.store.get_source_transactions('helius',WALLET,START,NOW)[0]['transaction']['signatures'][0],'not-normalized')

    def test_prune_more_than_sqlite_parameter_limit_uses_chunks(self):
        transactions=[raw(f'sig-{index}') for index in range(1100)]
        self.store.put_source_transactions('helius',WALLET,transactions)
        result=self.save([receipt(item) for item in transactions],transactions)
        self.assertEqual((result['receipts_saved'],result['raw_pruned']),(1100,1100))
        self.assertEqual(len(load_receipts(self.store,WALLET,START,NOW)),1100)

    def test_half_open_window_future_and_old_receipts_are_rejected(self):
        for stamp in (START-timedelta(seconds=1),NOW,NOW+timedelta(seconds=1)):
            transaction=raw(at=stamp)
            self.store.put_source_transactions('helius',WALLET,[transaction])
            with self.subTest(stamp=stamp),self.assertRaisesRegex(ValueError,'fora da janela'):
                self.save([receipt(transaction)],[transaction])
            self.assertEqual(self.count_raw(),1)
            self.store.db.execute("DELETE FROM source_transactions WHERE address=?",(WALLET,));self.store.db.commit()

    def test_proofs_are_immutable_and_missing_manifested_receipt_blocks_coverage(self):
        transaction=raw();self.save([receipt(transaction)],[transaction])
        for table in ('profit_receipts','profit_receipt_manifests'):
            for operation in ('UPDATE','DELETE'):
                sql=(f"UPDATE {table} SET payload=payload" if operation=='UPDATE' else f'DELETE FROM {table}')
                with self.subTest(table=table,operation=operation),self.assertRaises(sqlite3.IntegrityError):
                    self.store.db.execute(sql)
                self.store.db.rollback()
        self.store.db.execute('DROP TRIGGER immutable_profit_receipts_delete')
        self.store.db.execute('DELETE FROM profit_receipts');self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'Recibo esperado ausente'):
            load_receipts(self.store,WALLET,START,NOW)

    def test_corrupt_receipt_or_manifest_fails_before_replay(self):
        transaction=raw();self.save([receipt(transaction)],[transaction])
        self.store.db.execute('DROP TRIGGER immutable_profit_receipts_update')
        self.store.db.execute('UPDATE profit_receipts SET payload=?',(_pack_payload('{}'),));self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'Hash do recibo'):
            load_receipts(self.store,WALLET,START,NOW)

    def test_corrupt_manifest_blocks_replay(self):
        transaction=raw();self.save([receipt(transaction)],[transaction])
        self.store.db.execute('DROP TRIGGER immutable_profit_receipt_manifests_update')
        self.store.db.execute('UPDATE profit_receipt_manifests SET payload=?',(_pack_payload('{}'),));self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'Hash do manifest'):
            load_receipts(self.store,WALLET,START,NOW)

    def test_private_runs_are_hashed_compressed_immutable_and_latest_by_date(self):
        self.assertIsNone(latest_run(self.store))
        first={'started_at':START,'ranking':[{'wallet':WALLET,'score':Decimal('70.1')}]}
        second={'started_at':NOW,'ranking':[{'wallet':OTHER,'score':Decimal('60')}]}
        identity=save_run(self.store,first)
        self.assertEqual(identity,save_run(self.store,json.loads(dumps(first))))
        save_run(self.store,second)
        self.assertEqual(latest_run(self.store),json.loads(dumps(second)))
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute('UPDATE profit_runs SET payload=payload')
        self.store.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute('DELETE FROM profit_runs')
        self.store.db.rollback()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM profit_runs').fetchone()[0],2)

    def test_snapshot_hash_detects_payload_corruption(self):
        save_run(self.store,{'started_at':NOW,'ranking':[]})
        self.store.db.execute('DROP TRIGGER immutable_profit_runs_update')
        self.store.db.execute('UPDATE profit_runs SET payload=?',(_pack_payload('{}'),));self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'Hash da fotografia'):
            latest_run(self.store)

    def test_nested_caller_transaction_can_roll_back_receipts_and_pruning_together(self):
        transaction=raw();self.store.put_source_transactions('helius',WALLET,[transaction])
        ensure_schema(self.store)
        self.store.db.execute('BEGIN')
        self.save([receipt(transaction)],[transaction])
        self.assertEqual(self.count_raw(),0)
        self.store.db.rollback()
        self.assertEqual(self.count_raw(),1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM profit_receipts').fetchone()[0],0)


if __name__=='__main__':unittest.main()
