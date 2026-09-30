import unittest

from arb import record
from arb.decide import final_offer
from arb.owed import compute_owed

C, R = "pty_claim1", "pty_resp01"


def parties(ct="person", rt="business"):
    return [{"partyId": C, "role": "claimant", "type": ct}, {"partyId": R, "role": "respondent", "type": rt}]


def offers(c, r):
    return [{"partyId": C, "amount": c, "sealedAt": "2026-01-01T00:00:00Z"},
            {"partyId": R, "amount": r, "sealedAt": "2026-01-01T00:00:00Z"}]


class FinalOffer(unittest.TestCase):
    def test_closer_offer_wins_in_full(self):
        d = final_offer(50000, offers(90000, 45000), parties())
        self.assertEqual((d["mechanism"], d["winningPartyId"], d["outcome"]), ("final_offer", R, 45000))
        d = final_offer(50000, offers(52000, 10000), parties())
        self.assertEqual((d["winningPartyId"], d["outcome"]), (C, 52000))

    def test_tie_person_beats_business_either_role(self):
        # owed 50000; offers 40000 and 60000 are both 10000 away
        d = final_offer(50000, offers(40000, 60000), parties("person", "business"))
        self.assertEqual((d["winningPartyId"], d["outcome"]), (C, 40000))
        d = final_offer(50000, offers(40000, 60000), parties("business", "person"))
        self.assertEqual((d["winningPartyId"], d["outcome"]), (R, 60000))

    def test_tie_between_two_people_goes_to_claimant(self):
        d = final_offer(50000, offers(40000, 60000), parties("person", "person"))
        self.assertEqual((d["winningPartyId"], d["outcome"]), (C, 40000))
        d = final_offer(50000, offers(60000, 40000), parties("person", "person"))
        self.assertEqual((d["winningPartyId"], d["outcome"]), (C, 60000))

    def test_tie_between_two_businesses_goes_to_claimant(self):
        d = final_offer(50000, offers(40000, 60000), parties("business", "business"))
        self.assertEqual(d["winningPartyId"], C)

    def test_equal_offers_pay_that_amount(self):
        d = final_offer(1, offers(70000, 70000), parties())
        self.assertEqual(d["outcome"], 70000)

    def test_missing_offer_is_human_review(self):
        d = final_offer(50000, offers(1, 2)[:1], parties())
        self.assertEqual(d["mechanism"], "human_review")
        self.assertNotIn("winningPartyId", d)
        self.assertEqual(final_offer(50000, [], parties())["mechanism"], "human_review")

    def test_no_majority_that_changes_outcome_is_human_review(self):
        # offers 30000 / 80000: the switch point is 55000. Range 20000..90000 straddles it.
        d = final_offer(40000, offers(30000, 80000), parties(), owed_range=(20000, 90000))
        self.assertEqual(d["mechanism"], "human_review")

    def test_no_majority_that_does_not_change_outcome_is_ignored(self):
        # whole range is closer to the claimant's 30000 offer
        d = final_offer(40000, offers(30000, 80000), parties(), owed_range=(20000, 50000))
        self.assertEqual((d["mechanism"], d["winningPartyId"], d["outcome"]), ("final_offer", C, 30000))

    def test_range_exactly_on_the_switch_point_needs_review(self):
        # at 55000 it is a tie and the person wins (claimant here); above it the business wins
        d = final_offer(40000, offers(30000, 80000), parties(), owed_range=(40000, 56000))
        self.assertEqual(d["mechanism"], "human_review")
        d = final_offer(40000, offers(30000, 80000), parties(), owed_range=(40000, 55000))
        self.assertEqual(d["mechanism"], "final_offer")

    def test_range_review_is_exact_against_brute_force(self):
        offs = offers(30000, 80000)
        for low in range(0, 100001, 5000):
            for high in range(low, 100001, 5000):
                point = (low + high) // 2
                d = final_offer(point, offs, parties(), owed_range=(low, high))
                a = final_offer(low, offs, parties())
                b = final_offer(high, offs, parties())
                same = (a["winningPartyId"], a["outcome"]) == (b["winningPartyId"], b["outcome"])
                self.assertEqual(d["mechanism"] == "final_offer", same, (low, high))

    def test_integers_only(self):
        for bad in (50000.0, True, "50000", None):
            with self.assertRaises(TypeError):
                final_offer(bad, offers(1, 2), parties())
        with self.assertRaises(TypeError):
            final_offer(5, offers(1.5, 2), parties())
        with self.assertRaises(ValueError):
            final_offer(-1, offers(1, 2), parties())

    def test_malformed_parties_and_offers_are_errors(self):
        with self.assertRaises(ValueError):
            final_offer(5, offers(1, 2), parties()[:1])
        with self.assertRaises(ValueError):
            final_offer(5, [{"partyId": "pty_other1", "amount": 1}, {"partyId": R, "amount": 2}], parties())
        with self.assertRaises(ValueError):
            final_offer(5, [{"partyId": C, "amount": 1}, {"partyId": C, "amount": 2}], parties())

    def test_output_has_only_schema_decision_keys(self):
        d = final_offer(50000, offers(1, 2), parties())
        self.assertLessEqual(set(d), {"mechanism", "winningPartyId", "outcome", "explanation"})


def f(side, result, amount=None, adverse=False):
    return {"side": side, "result": result, "amount": amount, "adverseInference": adverse}


class Owed(unittest.TestCase):
    def test_bench_case_01(self):
        # deposit 107000 withheld; carpet and paint rejected; cleaning accepted
        o = compute_owed([f("claimant", "accepted", 107000), f("respondent", "rejected", 65000),
                          f("respondent", "rejected", 30000), f("respondent", "accepted", 12000)])
        self.assertEqual(o, {"low": 95000, "high": 95000, "point": 95000})

    def test_sum_claimant_minus_respondent_clamped_at_zero(self):
        o = compute_owed([f("claimant", "accepted", 10000), f("respondent", "accepted", 25000)])
        self.assertEqual(o, {"low": 0, "high": 0, "point": 0})

    def test_no_majority_sets_the_range_but_not_the_point(self):
        o = compute_owed([f("claimant", "accepted", 100000), f("respondent", "no_majority", 20000),
                          f("claimant", "no_majority", 5000), f("respondent", "accepted", 10000)])
        self.assertEqual(o, {"low": 70000, "high": 95000, "point": 90000})

    def test_low_is_clamped_to_zero_but_point_stays_inside(self):
        o = compute_owed([f("claimant", "accepted", 10000), f("respondent", "no_majority", 50000)])
        self.assertEqual(o, {"low": 0, "high": 10000, "point": 10000})
        self.assertTrue(o["low"] <= o["point"] <= o["high"])

    def test_adverse_inference_credits_nothing_to_the_withholder(self):
        base = [f("claimant", "accepted", 100000)]
        withheld = f("respondent", "accepted", 40000, adverse=True)
        self.assertEqual(compute_owed(base + [withheld])["point"], 100000)
        self.assertEqual(compute_owed(base + [f("respondent", "no_majority", 40000, adverse=True)]),
                         {"low": 100000, "high": 100000, "point": 100000})

    def test_no_amount_findings_change_nothing(self):
        self.assertEqual(compute_owed([f("claimant", "accepted"), f("respondent", "no_majority", None)]),
                         {"low": 0, "high": 0, "point": 0})

    def test_order_of_findings_never_matters(self):
        fs = [f("claimant", "accepted", 9), f("respondent", "accepted", 4), f("respondent", "no_majority", 3)]
        self.assertEqual(compute_owed(fs), compute_owed(list(reversed(fs))))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            compute_owed([f("claimant", "accepted", 1.5)])
        with self.assertRaises(ValueError):
            compute_owed([f("claimant", "accepted", -1)])
        with self.assertRaises(ValueError):
            compute_owed([f("judge", "accepted", 1)])
        with self.assertRaises(ValueError):
            compute_owed([f("claimant", "maybe", 1)])


class Record(unittest.TestCase):
    def test_canonical_json_ignores_key_order_and_spacing(self):
        self.assertEqual(record.canonical_json({"b": 1, "a": [1, {"d": 2, "c": 3}]}),
                         '{"a":[1,{"c":3,"d":2}],"b":1}')
        self.assertEqual(record.canonical_json({"a": 1, "b": 2}), record.canonical_json({"b": 2, "a": 1}))

    def test_floats_and_odd_types_are_rejected(self):
        for bad in ({"a": 1.0}, {"a": [float("nan")]}, {1: "x"}, {"a": {1, 2}}):
            with self.assertRaises(TypeError):
                record.canonical_json(bad)

    def test_chain_known_value_and_shape(self):
        r = record.chain({"a": 1})
        self.assertEqual(r["previous"], None)
        self.assertEqual(r["sha256"], record.sha256_hex('\n{"a":1}'))
        r2 = record.chain({"a": 1}, r["sha256"])
        self.assertEqual(r2["previous"], r["sha256"])
        self.assertNotEqual(r2["sha256"], r["sha256"])

    def test_previous_hash_and_body_both_change_the_hash(self):
        p = "a" * 64
        self.assertNotEqual(record.chain({"a": 1}, p)["sha256"], record.chain({"a": 1}, "b" * 64)["sha256"])
        self.assertNotEqual(record.chain({"a": 1}, p)["sha256"], record.chain({"a": 2}, p)["sha256"])

    def test_verify(self):
        body = {"a": 1}
        rec = record.chain(body, "c" * 64)
        self.assertTrue(record.verify(body, rec))
        self.assertFalse(record.verify({"a": 2}, rec))

    def test_bad_previous_is_rejected(self):
        for bad in ("abc", "G" * 64, 5):
            with self.assertRaises(ValueError):
                record.chain({"a": 1}, bad)


if __name__ == "__main__":
    unittest.main()
