"""Seleção económica preserva rotação e não renova evidência antiga."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from wallets.models import dumps, iso
from wallets.profit_queue import enqueue, select, checked
from wallets.storage import Store, _pack_payload

NOW=datetime(2026,10,30,12,tzinfo=timezone.utc)


def candidate(letter,pnl=1,origins=(),at=NOW):
    return {'wallet':letter*44,'origins':list(origins),
            'discovery':{'provider_pnl_usd':pnl,'observed_at':iso(at)}}


class ProfitQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory=TemporaryDirectory()
        self.store=Store(Path(self.directory.name)/'state.sqlite')

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_origins_accumulate_across_discoveries_and_expire_individually(self):
        enqueue(self.store,[candidate('A',origins=['token-one'])],NOW)
        later=NOW+timedelta(days=2)
        enqueue(self.store,[candidate('A',origins=['token-two'],at=later)],later)
        row=select(self.store,[],later,1)[0]
        self.assertEqual(row['origins'],['token-one','token-two'])
        self.assertEqual(row['discovery']['distinct_token_origins'],2)
        self.assertEqual(row['origin_observed_at']['token-one'],iso(NOW))
        after=NOW+timedelta(days=8)
        row=select(self.store,[],after,1)[0]
        self.assertEqual(row['origins'],['token-two'])
        self.assertEqual(row['discovery']['distinct_token_origins'],1)

    def test_rediscovery_refreshes_only_the_origins_actually_seen_again(self):
        enqueue(self.store,[candidate('A',origins=['one','two'])],NOW)
        later=NOW+timedelta(days=6)
        enqueue(self.store,[candidate('A',origins=['two'],at=later)],later)
        row=select(self.store,[],NOW+timedelta(days=8),1)[0]
        self.assertEqual(row['origins'],['two'])
        self.assertEqual(row['origin_observed_at']['two'],iso(later))

    def test_manual_request_preserves_existing_evidence_without_renewing_it(self):
        enqueue(self.store,[candidate('A',42,['one'])],NOW)
        later=NOW+timedelta(days=8)
        enqueue(self.store,[{'wallet':'A'*44,'origins':[]}],later,manual=True)
        row=select(self.store,[],later,1)[0]
        self.assertTrue(row['manual'])
        self.assertEqual(row['origins'],[])
        self.assertEqual(row['discovery']['observed_at'],iso(NOW))
        enqueue(self.store,[{'wallet':'B'*44,'origins':['not-a-discovery']}],later,manual=True)
        rows=select(self.store,[],later,2)
        self.assertTrue(all(row['origins']==[] for row in rows))

    def test_legacy_uses_stored_timestamp_and_future_or_invalid_origins_do_not_rank(self):
        enqueue(self.store,[candidate('A',origins=['legacy'])],NOW)
        legacy={'wallet':'A'*44,'origins':['legacy']}
        self.store.db.execute('UPDATE profit_queue SET payload=? WHERE wallet=?',(_pack_payload(dumps(legacy)),'A'*44))
        self.store.db.commit()
        self.assertEqual(select(self.store,[],NOW+timedelta(days=1),1)[0]['origins'],['legacy'])
        late=NOW+timedelta(days=8)
        enqueue(self.store,[{'wallet':'A'*44,'origins':[]}],late,manual=True)
        self.assertEqual(select(self.store,[],late,1)[0]['origins'],[])
        malformed=candidate('B',999,origins=['future'])
        malformed['origin_observed_at']={'future':iso(late+timedelta(days=1)),'bad':'invalid'}
        enqueue(self.store,[malformed],late)
        row=next(row for row in select(self.store,[],late,3) if row['wallet']=='B'*44)
        self.assertEqual(row['origins'],[])

    def test_recent_repetition_then_profit_rank_independently_of_insertion_order(self):
        rows=[candidate('A',10),candidate('B',1000),candidate('C',1,['one','two'])]
        for row in rows:enqueue(self.store,[row],NOW)
        selected=select(self.store,[],NOW,3)
        self.assertEqual([row['wallet'] for row in selected],['C'*44,'B'*44,'A'*44])
        for row in selected:checked(self.store,row,NOW)
        # Uma nova carteira tem oportunidade antes das já avaliadas, mesmo com PnL menor.
        selected=select(self.store,[candidate('D',1)],NOW+timedelta(hours=1),1)
        self.assertEqual(selected[0]['wallet'],'D'*44)

    def test_oldest_evaluation_manual_and_pending_priorities_are_preserved(self):
        a,b=candidate('A',1),candidate('B',1000)
        enqueue(self.store,[a,b],NOW)
        checked(self.store,a,NOW)
        checked(self.store,b,NOW+timedelta(hours=1))
        self.assertEqual(select(self.store,[],NOW+timedelta(hours=2),1)[0]['wallet'],a['wallet'])
        b['prefilter7']={'passed':True}
        checked(self.store,b,NOW+timedelta(hours=2))
        self.assertEqual(select(self.store,[candidate('C',10000)],NOW+timedelta(hours=3),1)[0]['wallet'],b['wallet'])
        enqueue(self.store,[{'wallet':'D'*44,'origins':[]}],NOW+timedelta(hours=3),manual=True)
        # Uma redescoberta não apaga o pedido manual pendente.
        enqueue(self.store,[candidate('D',0,at=NOW+timedelta(hours=3))],NOW+timedelta(hours=3))
        self.assertEqual(select(self.store,[],NOW+timedelta(hours=3),1)[0]['wallet'],'D'*44)

    def test_stale_future_and_nonfinite_profit_do_not_get_economic_priority(self):
        rows=[candidate('A',999999,at=NOW-timedelta(days=8)),
              candidate('B',999999,at=NOW+timedelta(days=1)),
              candidate('C','NaN'),candidate('D',10)]
        selected=select(self.store,rows,NOW,4)
        self.assertEqual(selected[0]['wallet'],'D'*44)


if __name__=='__main__':unittest.main()
