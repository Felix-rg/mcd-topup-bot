import subprocess
import textwrap
import unittest
from pathlib import Path


class StorefrontCheckoutFrontendTests(unittest.TestCase):
    @staticmethod
    def _run_node(assertions: str) -> None:
        root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            f"""
            const assert = require("assert");
            global.window = {{}};
            global.localStorage = {{ getItem: () => "", setItem() {{}}, removeItem() {{}} }};
            global.document = {{
                addEventListener() {{}},
                getElementById: () => null,
                querySelectorAll: () => [],
                querySelector: () => null,
            }};
            global.navigator = {{}};
            require(process.cwd() + "/web/js/topup.js");
            const checkout = window.__lixafaCheckoutFrontend;
            {assertions}
            """
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(
                "Node storefront checkout test failed\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

    def test_01_apply_is_disabled_while_quote_is_loading(self):
        self._run_node(
            """
            const state = checkout.deriveCheckoutControlState({
                sku: "ff50", method: "DANA", phone: "081234567890",
                loading: true, quoteReady: true,
            });
            assert.strictEqual(state.disabled, true);
            assert.strictEqual(state.message, "Tunggu perhitungan harga selesai");
            """
        )

    def test_02_missing_sku_blocks_apply_before_request(self):
        self._run_node(
            """
            const state = checkout.deriveCheckoutControlState({
                sku: " ", method: "DANA", phone: "081234567890", quoteReady: true,
            });
            assert.strictEqual(state.disabled, true);
            assert.strictEqual(state.missingSku, true);
            """
        )

    def test_03_missing_payment_method_blocks_apply_before_request(self):
        self._run_node(
            """
            const state = checkout.deriveCheckoutControlState({
                sku: "ff50", method: " ", phone: "081234567890", quoteReady: true,
            });
            assert.strictEqual(state.disabled, true);
            assert.strictEqual(state.missingMethod, true);
            """
        )

    def test_04_quote_payload_uses_canonical_sku_without_frontend_totals(self):
        self._run_node(
            """
            const payload = checkout.buildCheckoutQuotePayload({
                sku: "  FF50 ", method: "DANA", promoCode: "FFDANA",
                phone: "081234567890", targetId: "12345",
                subtotal: 7416, total: 1, product_price: 1,
            });
            assert.strictEqual(payload.sku, "ff50");
            assert.strictEqual("subtotal" in payload, false);
            assert.strictEqual("total" in payload, false);
            assert.strictEqual("product_price" in payload, false);
            assert.strictEqual(checkout.hasAuthoritativeQuoteTotals({ final_price: 0, payment_fee: 1000 }), false);
            assert.strictEqual(checkout.hasAuthoritativeQuoteTotals({ final_price: 0, payment_fee: 1000, total: 1000 }), true);
            """
        )

    def test_05_quote_payload_uses_canonical_payment_and_voucher_codes(self):
        self._run_node(
            """
            const payload = checkout.buildCheckoutQuotePayload({
                sku: "ff50", method: " dana ", promoCode: " ffdana ",
                phone: " 081234567890 ", targetId: " 9988 ",
            });
            assert.strictEqual(payload.method, "DANA");
            assert.strictEqual(payload.promo_code, "FFDANA");
            assert.strictEqual(payload.phone, "081234567890");
            assert.strictEqual(payload.target_id, "9988");
            assert.strictEqual("member" in payload, false);
            assert.strictEqual("customer_id" in payload, false);
            assert.deepStrictEqual(checkout.buildCheckoutQuoteHeaders(""), {
                "Content-Type": "application/json",
            });
            assert.deepStrictEqual(checkout.buildCheckoutQuoteHeaders(" customer-jwt "), {
                "Content-Type": "application/json",
                "customer-token": "customer-jwt",
            });
            """
        )

    def test_06_stale_quote_response_cannot_commit_to_new_context(self):
        self._run_node(
            """
            assert.strictEqual(checkout.isQuoteResponseCurrent(8, 8, "old", "old"), true);
            assert.strictEqual(checkout.isQuoteResponseCurrent(8, 9, "old", "old"), false);
            assert.strictEqual(checkout.isQuoteResponseCurrent(8, 8, "old", "new"), false);
            """
        )

    def test_07_product_change_clears_old_promo_and_queues_typed_code(self):
        self._run_node(
            """
            const transition = checkout.selectionPromoTransition(" ffdana ", "OLDPROMO");
            assert.strictEqual(transition.clearAppliedPromo, true);
            assert.strictEqual(transition.shouldRevalidate, true);
            assert.strictEqual(transition.revalidateCode, "FFDANA");
            """
        )

    def test_08_payment_change_revalidates_the_previously_applied_code(self):
        self._run_node(
            """
            const transition = checkout.selectionPromoTransition("", " ffdana ");
            assert.strictEqual(transition.clearAppliedPromo, true);
            assert.strictEqual(transition.shouldRevalidate, true);
            assert.strictEqual(transition.revalidateCode, "FFDANA");
            """
        )

    def test_09_structured_nested_reason_code_message_is_used(self):
        self._run_node(
            """
            const explicit = checkout.structuredQuoteError({
                detail: {
                    reason_code: "PAYMENT_METHOD_NOT_ELIGIBLE",
                    message: "Promo hanya berlaku untuk metode tertentu.",
                },
            }, "fallback");
            assert.strictEqual(explicit.reasonCode, "PAYMENT_METHOD_NOT_ELIGIBLE");
            assert.strictEqual(explicit.message, "Promo hanya berlaku untuk metode tertentu.");
            const mapped = checkout.structuredQuoteError({ reason_code: "PRODUCT_NOT_ELIGIBLE" }, "fallback");
            assert.strictEqual(mapped.message, "Promo tidak berlaku untuk produk yang dipilih.");
            const member = checkout.structuredQuoteError({
                reason_code: "MEMBER_LOGIN_REQUIRED",
                detail: "Kode promo tidak berlaku untuk transaksi ini.",
            }, "fallback");
            assert.strictEqual(member.message, "Masuk ke akun untuk menggunakan promo ini.");
            assert.strictEqual(
                checkout.structuredQuoteError({ reason_code: "GUEST_ONLY_PROMO" }, "fallback").message,
                "Promo ini hanya berlaku untuk checkout tanpa akun.",
            );
            assert.strictEqual(
                checkout.structuredQuoteError({ reason_code: "NEW_CUSTOMER_ONLY" }, "fallback").message,
                "Promo ini hanya berlaku untuk pelanggan baru.",
            );
            assert.strictEqual(
                checkout.structuredQuoteError({ reason_code: "EXISTING_CUSTOMER_ONLY" }, "fallback").message,
                "Promo ini hanya berlaku untuk pelanggan yang sudah pernah bertransaksi.",
            );
            assert.strictEqual(
                checkout.structuredQuoteError({ reason_code: "CUSTOMER_NOT_TARGETED" }, "fallback").message,
                "Promo ini tidak ditujukan untuk akun atau nomor Anda.",
            );
            assert.strictEqual(
                checkout.structuredQuoteError({ reason_code: "ACCOUNT_INACTIVE" }, "fallback").message,
                "Akun Anda tidak aktif dan tidak dapat menggunakan promo member.",
            );
            assert.strictEqual(
                checkout.structuredQuoteError({ reason_code: "PHONE_REQUIRED" }, "fallback").message,
                "Masukkan nomor WhatsApp untuk menggunakan promo ini.",
            );
            assert.strictEqual(
                checkout.structuredQuoteError({ reason_code: "TARGET_ID_REQUIRED" }, "fallback").message,
                "Masukkan ID tujuan untuk menggunakan promo ini.",
            );
            """
        )

    def test_10_ffdana_ff50_dana_respects_authoritative_free_checkout(self):
        self._run_node(
            """
            const context = {
                key: "ff50-dana-ffdana", sku: "ff50", method: "DANA",
                promoCode: "FFDANA", phone: "081234567890", targetId: "12345",
            };
            const quote = checkout.normalizeCheckoutQuote({
                original_price: 7416,
                final_price: 0,
                discount_amount: 7416,
                payment_fee: 0,
                total: 0,
                free_checkout: true,
                method: "FREE_PROMO",
                promo: { code: "FFDANA", title: "KHUSUS PENGGUNA DANA" },
            }, context, "test");
            assert.strictEqual(quote.base_amount, 0);
            assert.strictEqual(quote.original_amount, 7416);
            assert.strictEqual(quote.discount_amount, 7416);
            assert.strictEqual(quote.payment_fee, 0);
            assert.strictEqual(quote.total, 0);
            assert.strictEqual(checkout.shouldUseFreeCheckout(quote), true);
            assert.strictEqual(checkout.shouldUseFreeCheckout({
                free_checkout: false, payment_fee: 1000, total: 1000,
            }), false);
            """
        )

    def test_11_automatic_payment_fallback_clears_and_revalidates_promo(self):
        self._run_node(
            """
            const transition = checkout.paymentMethodPromoTransition(
                " qris ", " ffdana ", "FFDANA",
            );
            assert.strictEqual(transition.method, "QRIS");
            assert.strictEqual(transition.clearAppliedPromo, true);
            assert.strictEqual(transition.shouldRevalidate, true);
            assert.strictEqual(transition.revalidateCode, "FFDANA");
            const noPromo = checkout.paymentMethodPromoTransition("", "", "");
            assert.strictEqual(noPromo.method, "");
            assert.strictEqual(noPromo.shouldRevalidate, false);
            """
        )

    def test_12_payment_channel_error_state_requires_retry(self):
        self._run_node(
            """
            const failed = checkout.derivePaymentChannelLoadState({
                loading: false,
                error: "Provider tidak tersedia",
                channels: [],
            });
            assert.strictEqual(failed.canCheckout, false);
            assert.strictEqual(failed.showRetry, true);
            const ready = checkout.derivePaymentChannelLoadState({
                channels: [
                    { code: "DANA", active: true, maintenance: false },
                    { code: "OVO", active: false, maintenance: false },
                    { code: "QRIS", active: true, maintenance: true },
                ],
            });
            assert.strictEqual(ready.availableCount, 1);
            assert.strictEqual(ready.canCheckout, true);
            """
        )

    def test_13_stale_payment_channel_request_cannot_replace_new_state(self):
        self._run_node(
            """
            assert.strictEqual(checkout.isPaymentChannelResponseCurrent(4, 4), true);
            assert.strictEqual(checkout.isPaymentChannelResponseCurrent(3, 4), false);
            """
        )

    def test_14_checkout_has_no_static_channel_or_fee_fallback(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "web" / "js" / "topup.js").read_text(encoding="utf-8")
        html = (root / "web" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("fallbackPaymentChannels", source)
        self.assertNotIn("return 4500", source)
        self.assertNotIn("price * 0.007", source)
        self.assertNotIn("price * 0.03", source)
        self.assertIn("data-retry-payment-channels", source)
        self.assertIn('id="method" value=""', html)
        self.assertNotIn('data-payment-method="QRIS"', html)
        self.assertIn("Memuat metode pembayaran...", html)


if __name__ == "__main__":
    unittest.main()
