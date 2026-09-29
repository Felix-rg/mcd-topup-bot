import subprocess
import textwrap
import unittest
from pathlib import Path


class DashboardValidationHelperTests(unittest.TestCase):
    def test_validation_loc_parser_and_integer_array_helpers(self):
        root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            r"""
            global.window = {
                location: { href: "", origin: "http://localhost" },
                addEventListener: () => {},
            };
            global.localStorage = { getItem: () => "token" };
            global.document = {
                getElementById: () => null,
                addEventListener: () => {},
                querySelector: () => null,
                querySelectorAll: () => [],
            };
            global.bootstrap = { Modal: function Modal() { return { show() {}, hide() {} }; } };
            global.confirm = () => true;

            require(process.cwd() + "/web/js/dashboard.js");
            const helpers = window.__lixafaPromoValidation;

            function assert(condition, message) {
                if (!condition) throw new Error(message);
            }

            assert(helpers.getValidationField(["body", "discount_value"]) === "discount_value", "scalar loc field");
            assert(helpers.getValidationField(["body", "active_days", 1]) === "active_days", "array loc field");
            assert(helpers.getValidationIndex(["body", "active_days", 1]) === 1, "array loc index");
            assert(helpers.getValidationField(["body", "target_option_ids", 1]) === "target_option_ids", "target array loc field");
            assert(helpers.getValidationIndex(["body", "target_option_ids", 1]) === 1, "target array loc index");
            assert(helpers.getValidationField(["body", "rules", 0, "product_ids", 2]) === "product_ids", "nested loc field");
            assert(helpers.getValidationIndex(["body", "rules", 0, "product_ids", 2]) === 2, "nested loc index");

            const message = helpers.detailToMessage([
                {
                    type: "int_parsing",
                    loc: ["body", "target_option_ids", 1],
                    msg: "Input should be a valid integer, unable to parse string as an integer",
                    input: ""
                },
                {
                    type: "int_parsing",
                    loc: ["body", "target_option_ids", 1],
                    msg: "Input should be a valid integer, unable to parse string as an integer",
                    input: ""
                },
                {
                    type: "int_parsing",
                    loc: ["body", "rules", 0, "product_ids", 2],
                    msg: "Input should be a valid integer, unable to parse string as an integer",
                    input: ""
                }
            ]);
            assert(message.includes("Target Promo item ke-2 memiliki ID tidak valid."), "target id label");
            assert(message.includes("Produk item ke-3 harus berupa angka bulat."), "nested product label");
            assert(!message.includes("1 harus berupa angka"), "numeric index must not be field label");
            assert(!message.includes("Input should be a valid integer"), "raw pydantic message must not be duplicated");
            assert(message.split("Target Promo item ke-2").length === 2, "duplicate target error must be collapsed");

            assert(JSON.stringify(helpers.parseIntegerArray(["1", "2", "3"])) === JSON.stringify([1, 2, 3]), "numeric strings");
            assert(JSON.stringify(helpers.parseIntegerArray([{ id: 4 }, { id: "5" }])) === JSON.stringify([4, 5]), "object choices");
            let emptyThrown = false;
            try { helpers.parseIntegerArray(["", null, undefined], "Target Promo"); } catch (error) {
                emptyThrown = error.message.includes("item ke-1");
            }
            assert(emptyThrown, "empty integer-array item is reported instead of silently discarded");
            let missingObjectIdThrown = false;
            try { helpers.parseIntegerArray([{ value: "ff50" }], "Target Promo"); } catch (error) {
                missingObjectIdThrown = error.message.includes("item ke-1");
            }
            assert(missingObjectIdThrown, "UI object without an integer id is rejected");
            let invalidThrown = false;
            try { helpers.parseIntegerArray(["SKU-FF50"]); } catch (_) { invalidThrown = true; }
            assert(invalidThrown, "SKU string is not coerced into integer array");
            assert(
                JSON.stringify(helpers.parseStringArray(["ff50", "QRIS", { sku: "ff100" }, { code: "BRIVA" }]))
                    === JSON.stringify(["ff50", "QRIS", "ff100", "BRIVA"]),
                "SKU and payment codes remain strings"
            );
            assert(JSON.stringify(helpers.normalizePromoActiveDays(["mon", "tue", "sun"])) === JSON.stringify([1, 2, 0]), "legacy day names");
            assert(JSON.stringify(helpers.normalizePromoActiveDays(["1", 2, "0"])) === JSON.stringify([1, 2, 0]), "numeric day values");
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
            self.fail(f"Node validation helper test failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")

    def test_canonical_create_and_edit_payloads_from_dom_values(self):
        root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            r"""
            const elements = {};
            function field(id, value = "", extra = {}) {
                elements[id] = Object.assign({
                    value,
                    checked: false,
                    querySelectorAll: () => []
                }, extra);
                return elements[id];
            }
            function multi(id, values) {
                return field(id, "", {
                    selectedOptions: values.map((value) => ({
                        value: String(value), textContent: String(value), dataset: {}
                    }))
                });
            }
            function checks(id, values) {
                return field(id, "", {
                    querySelectorAll: (selector) => selector.includes(":checked")
                        ? values.map((value) => ({ value: String(value), checked: true }))
                        : []
                });
            }
            function setup(prefix) {
                const defaults = {
                    usage_mode: "unlimited", target_scope: "all", cta_url: "",
                    customer_description: "", description: "", promo_type: "banner",
                    rule_type: "content", discount_type: "", status: "draft",
                    target_value: "", customer_segment: "all", customer_ids: "", title: "  Promo Test  ",
                    internal_code: "", code: "STALE-CODE", internal_description: "",
                    admin_notes: "", badge: "", discount_value: "", max_discount: "",
                    minimum_transaction: "", special_price: "", rounding_rule: "none",
                    usage_limit: "", quota_daily: "", budget_limit: "", max_per_customer: "",
                    max_per_customer_daily: "", max_per_phone: "", max_per_target: "",
                    stackable: "1", exclusive: "0", max_promotions_per_order: "1",
                    priority: "0", cta_text: "", image_url: "", starts_at: "", ends_at: "",
                    timezone: "Asia/Jakarta", daily_start: "", daily_end: "", display_order: "0"
                };
                Object.entries(defaults).forEach(([suffix, value]) => field(`${prefix}_${suffix}`, value));
                field(`${prefix}_allow_external_cta`, "", { checked: false });
                field(`${prefix}_active`, "", { checked: false });
                field(`${prefix}_show_on_website`, "", { checked: true });
                multi(`${prefix}_target_values`, []);
                multi(`${prefix}_target_exclusions`, []);
                checks(`${prefix}_payment_methods`, []);
                checks(`${prefix}_active_days`, [1, 2, 3, 4, 5, 6, 0]);
                checks(`${prefix}_placements`, ["promo_cards"]);
                elements[`${prefix}_target_value`].selectedOptions = [
                    { value: "", dataset: {}, textContent: "" }
                ];
            }

            setup("promo");
            setup("edit_promo");
            global.window = {
                location: { href: "", origin: "http://localhost" },
                addEventListener: () => {},
            };
            global.localStorage = { getItem: () => "token", removeItem: () => {} };
            global.document = {
                getElementById: (id) => elements[id] || null,
                addEventListener: () => {},
                querySelector: () => null,
                querySelectorAll: () => [],
                activeElement: null,
            };
            global.bootstrap = { Modal: function Modal() { return { show() {}, hide() {} }; } };
            global.confirm = () => true;

            require(process.cwd() + "/web/js/dashboard.js");
            const helpers = window.__lixafaPromoValidation;
            function assert(condition, message) {
                if (!condition) throw new Error(message);
            }

            const banner = helpers.getPromoPayload("promo");
            assert(banner.title === "Promo Test", "title is trimmed");
            assert(banner.promo_type === "banner" && banner.rule_type === "content", "canonical banner type");
            assert(banner.code === null, "stale voucher code is omitted for a banner");
            assert(banner.target_value === null, "all-products target has null legacy key");
            assert(JSON.stringify(banner.target_option_ids) === "[]", "all-products target has no relation ids");
            assert(banner.discount_type === null && banner.discount_value === null, "banner pricing is null");
            assert(banner.starts_at === null && banner.daily_start_time === null, "empty schedules are null");
            assert(banner.internal_code === null && banner.description === null && banner.image_url === null, "empty optional strings are null");
            assert(JSON.stringify(banner.active_days) === "[1,2,3,4,5,6,0]", "day values stay integers");
            assert(banner.placements[0] === "promo_cards", "canonical promo-card placement");

            elements.edit_promo_promo_type.value = "payment_method";
            elements.edit_promo_rule_type.value = "price";
            elements.edit_promo_discount_type.value = "fixed";
            elements.edit_promo_discount_value.value = "5000";
            elements.edit_promo_target_scope.value = "sku";
            Object.assign(elements.edit_promo_target_value, {
                value: "SKU-FF50",
                selectedOptions: [{
                    value: "SKU-FF50",
                    dataset: { optionId: "71" },
                    textContent: "Free Fire 50"
                }]
            });
            multi("edit_promo_target_values", [72, 71]);
            multi("edit_promo_target_exclusions", [73]);
            checks("edit_promo_payment_methods", ["qris", "DANA"]);
            const payment = helpers.getPromoPayload("edit_promo");
            assert(payment.promo_type === "payment_method" && payment.rule_type === "price", "canonical payment type");
            assert(payment.target_value === "SKU-FF50", "SKU remains a string key");
            assert(JSON.stringify(payment.target_option_ids) === "[71,72]", "catalog ids are integer and unique");
            assert(JSON.stringify(payment.excluded_target_option_ids) === "[73]", "exclusion ids are integer");
            assert(JSON.stringify(payment.payment_methods) === '["QRIS","DANA"]', "payment codes stay strings");
            assert(payment.discount_value === 5000 && payment.max_discount === null, "fixed discount is canonical");

            elements.edit_promo_customer_segment.value = "specific";
            elements.edit_promo_customer_ids.value = "12, 12, 18";
            const specific = helpers.getPromoPayload("edit_promo");
            assert(specific.customer_segment === "specific", "specific segment remains canonical");
            assert(JSON.stringify(specific.customer_ids) === "[12,18]", "specific target IDs are unique integers");
            elements.edit_promo_customer_segment.value = "all";
            const changedToAll = helpers.getPromoPayload("edit_promo");
            assert(changedToAll.customer_segment === "all", "all segment remains canonical");
            assert(JSON.stringify(changedToAll.customer_ids) === "[]", "non-specific segment never leaks stale targets");

            [banner, payment].forEach((payload) => {
                ["promotion_type", "target_values", "target_exclusions", "status", "voucher_code", "maximum_discount", "quota_total"]
                    .forEach((legacy) => assert(!(legacy in payload), `legacy field leaked: ${legacy}`));
            });
            assert(helpers.normalizedPromoFormStatus({ status: "live", active: 1 }) === "active", "runtime live maps to editable active");
            assert(helpers.normalizedPromoFormStatus({ status: "scheduled", active: 1 }) === "active", "runtime scheduled maps to editable active");
            assert(helpers.normalizedPromoFormStatus({ status: "expired", active: 0 }) === "ended", "runtime expired maps to ended");
            assert(
                helpers.normalizedPromoFormStatus({ lifecycle_status: "active", runtime_status: "expired", active: 1 }) === "active",
                "configured lifecycle wins over runtime status"
            );
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
            self.fail(f"Node promo payload test failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")

    def test_flexible_eligibility_presets_rules_typed_values_and_summaries(self):
        root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            r"""
            const elements = {
                promo_customer_ids: { value: "12, 18", querySelectorAll: () => [] },
                edit_promo_customer_ids: { value: "", querySelectorAll: () => [] },
            };
            global.window = {
                location: { href: "", origin: "http://localhost" },
                addEventListener: () => {},
            };
            global.localStorage = { getItem: () => "token", removeItem: () => {} };
            global.document = {
                getElementById: (id) => elements[id] || null,
                addEventListener: () => {},
                querySelector: () => null,
                querySelectorAll: () => [],
                activeElement: null,
            };
            global.bootstrap = { Modal: function Modal() { return { show() {}, hide() {} }; } };
            global.confirm = () => true;

            require(process.cwd() + "/web/js/dashboard.js");
            const helpers = window.__lixafaPromoValidation;
            function assert(condition, message) {
                if (!condition) throw new Error(message);
            }
            function same(actual, expected, message) {
                assert(JSON.stringify(actual) === JSON.stringify(expected), `${message}: ${JSON.stringify(actual)}`);
            }

            const metadata = helpers.promoEligibilityFallbackMetadata();
            helpers.setPromoOptionsForValidation({ eligibility: metadata });

            // 1. Seluruh delapan preset tersedia dalam urutan UI yang stabil.
            same(
                metadata.presets.map((item) => item.value),
                ["all", "members_only", "guests_only", "first_purchase", "member_new", "existing", "specific", "custom"],
                "eight eligibility presets",
            );

            // 2. Preset adalah pengisi rule canonical, bukan enum engine kedua.
            const membersPreset = metadata.presets.find((item) => item.value === "members_only");
            helpers.setPromoEligibilityRulesForValidation("promo", membersPreset.rules, "members_only");
            const membersRules = helpers.serializePromoEligibilityRules("promo");
            same(membersRules, {
                version: 1,
                operator: "all",
                conditions: [
                    { field: "authentication_status", operator: "equals", value: "member" },
                    { field: "account_status", operator: "equals", value: "active" },
                ],
            }, "member preset fills canonical rules");

            // 3. Pembeli pertama tetap berbeda secara semantik dari Member baru.
            const firstPurchase = metadata.presets.find((item) => item.value === "first_purchase").rules;
            const memberNew = metadata.presets.find((item) => item.value === "member_new").rules;
            assert(firstPurchase.conditions.length === 1, "first purchase has one history condition");
            assert(firstPurchase.conditions[0].field === "successful_order_count", "first purchase uses SUCCESS history");
            assert(!firstPurchase.conditions.some((item) => item.field === "account_age_days"), "first purchase is not account age");
            assert(memberNew.conditions.some((item) => item.field === "authentication_status" && item.value === "member"), "member new requires member auth");
            assert(memberNew.conditions.some((item) => item.field === "account_age_days" && item.value === 7), "member new defaults to seven days");

            // 4. Test-state helper deterministically models add and remove condition operations.
            const baseRules = { version: 1, operator: "all", conditions: [
                { field: "authentication_status", operator: "equals", value: "member" },
            ] };
            helpers.setPromoEligibilityRulesForValidation("promo", baseRules);
            assert(helpers.serializePromoEligibilityRules("promo").conditions.length === 1, "initial condition state");
            const withAddedCondition = JSON.parse(JSON.stringify(baseRules));
            withAddedCondition.conditions.push({ field: "account_age_days", operator: "less_than_or_equal", value: "7" });
            helpers.setPromoEligibilityRulesForValidation("promo", withAddedCondition);
            assert(helpers.serializePromoEligibilityRules("promo").conditions.length === 2, "condition added");
            withAddedCondition.conditions.splice(0, 1);
            helpers.setPromoEligibilityRulesForValidation("promo", withAddedCondition);
            same(helpers.serializePromoEligibilityRules("promo").conditions, [
                { field: "account_age_days", operator: "less_than_or_equal", value: 7 },
            ], "condition removed without leaking UI state");

            // 5. ALL/ANY survives serialization and JSON round-trip.
            const anyRules = { version: 1, operator: "any", conditions: [
                { field: "successful_order_count", operator: "greater_than_or_equal", value: 20 },
                { field: "successful_order_total", operator: "greater_than_or_equal", value: 1000000 },
            ] };
            helpers.setPromoEligibilityRulesForValidation("edit_promo", anyRules);
            const serializedAny = helpers.serializePromoEligibilityRules("edit_promo");
            assert(serializedAny.operator === "any", "ANY operator serialized");
            same(helpers.parsePromoEligibilityRules(JSON.stringify(serializedAny)), {
                ...serializedAny, preset: "", invalid_json: false,
            }, "ANY rules parse round-trip");

            // 6. Tipe enum, integer, Rupiah, datetime, between, unary, identity, dan customer selector dikanonisasi.
            const typedRules = { version: 1, operator: "all", conditions: [
                { field: "authentication_status", operator: "equals", value: "member" },
                { field: "account_age_days", operator: "less_than_or_equal", value: "7" },
                { field: "successful_order_total", operator: "greater_than_or_equal", value: "500000" },
                { field: "account_created_at", operator: "greater_than_or_equal", value: "2026-07-20T10:00" },
                { field: "successful_order_count", operator: "between", value: ["1", "10"] },
                { field: "has_successful_order", operator: "is_true", value: true },
                { field: "normalized_phone", operator: "in", value: ["0812345678", "+62812345679"] },
                { field: "target_id", operator: "equals", value: " Game-ID-01 " },
                { field: "customer_id", operator: "in", value: [999] },
            ] };
            helpers.setPromoEligibilityRulesForValidation("promo", typedRules);
            const typed = helpers.serializePromoEligibilityRules("promo");
            const typedByField = Object.fromEntries(typed.conditions.map((item) => [item.field, item]));
            assert(typedByField.authentication_status.value === "member", "enum remains canonical string");
            assert(typedByField.account_age_days.value === 7, "integer input coerced");
            assert(typedByField.successful_order_total.value === 500000, "Rupiah input coerced");
            assert(!Number.isNaN(Date.parse(typedByField.account_created_at.value)), "datetime becomes ISO timestamp");
            same(typedByField.successful_order_count.value, [1, 10], "between has numeric min/max");
            assert(typedByField.has_successful_order.value === null, "unary operator sends null value");
            same(typedByField.normalized_phone.value, ["62812345678", "62812345679"], "phones normalized");
            assert(typedByField.target_id.value === "game-id-01", "target ID normalized");
            same(typedByField.customer_id.value, [12, 18], "customer rule uses secure selector IDs");
            assert(!JSON.stringify(typed).includes("_key"), "UI keys never enter payload rules");

            // 7. Ringkasan Indonesia menjelaskan aturan tanpa enum teknis.
            const memberSummary = helpers.promoEligibilitySummary(memberNew, "member_new");
            assert(memberSummary.includes("member aktif") && memberSummary.includes("maksimal 7 hari"), "member-new human summary");
            const customSummary = helpers.promoEligibilitySummary(anyRules, "custom");
            assert(customSummary.includes("salah satu") && customSummary.includes("Rp1.000.000"), "ANY/Rupiah human summary");
            ["authentication_status", "successful_order_total", "greater_than_or_equal", "member_new"]
                .forEach((raw) => assert(!`${memberSummary} ${customSummary}`.includes(raw), `raw enum hidden: ${raw}`));
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
            self.fail(
                "Node eligibility preset/type test failed\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

    def test_flexible_eligibility_edit_legacy_and_diagnostics_contract(self):
        root = Path(__file__).resolve().parents[1]
        dashboard_js = (root / "web" / "js" / "dashboard.js").read_text(encoding="utf-8")
        script = textwrap.dedent(
            r"""
            global.window = {
                location: { href: "", origin: "http://localhost" },
                addEventListener: () => {},
            };
            global.localStorage = { getItem: () => "token", removeItem: () => {} };
            global.document = {
                getElementById: () => null,
                addEventListener: () => {},
                querySelector: () => null,
                querySelectorAll: () => [],
                activeElement: null,
            };
            global.bootstrap = { Modal: function Modal() { return { show() {}, hide() {} }; } };
            global.confirm = () => true;

            require(process.cwd() + "/web/js/dashboard.js");
            const helpers = window.__lixafaPromoValidation;
            const metadata = helpers.promoEligibilityFallbackMetadata();
            helpers.setPromoOptionsForValidation({ eligibility: metadata });
            function assert(condition, message) {
                if (!condition) throw new Error(message);
            }
            function same(actual, expected, message) {
                assert(JSON.stringify(actual) === JSON.stringify(expected), `${message}: ${JSON.stringify(actual)}`);
            }

            // 8. Rule explicit dari response edit diparse, diinferensikan, dan dihydrate tanpa perubahan.
            const explicit = { version: 1, operator: "all", conditions: [
                { field: "authentication_status", operator: "equals", value: "member" },
                { field: "account_status", operator: "equals", value: "active" },
                { field: "account_age_days", operator: "less_than_or_equal", value: 14 },
                { field: "successful_order_count", operator: "equals", value: 0 },
            ] };
            const parsedExplicit = helpers.parsePromoEligibilityRules(JSON.stringify(explicit));
            assert(helpers.inferPromoEligibilityPreset(parsedExplicit, "explicit", "all") === "member_new", "explicit edit infers member-new presentation");
            helpers.setPromoEligibilityRulesForValidation("edit_promo", parsedExplicit, "member_new");
            same(helpers.serializePromoEligibilityRules("edit_promo"), explicit, "explicit edit round-trip");

            // 9. Adapter legacy mempertahankan arti enum new dan tidak menambah metadata UI ke rule.
            const legacy = helpers.legacyPromoEligibilityRules("new");
            assert(helpers.inferPromoEligibilityPreset(legacy, "legacy_adapter", "new") === "first_purchase", "legacy new maps to first purchase");
            assert(legacy.conditions[0].field === "successful_order_count" && legacy.conditions[0].value === 0, "legacy behavior remains SUCCESS-count based");
            helpers.setPromoEligibilityRulesForValidation("edit_promo", legacy, "first_purchase");
            const converted = helpers.serializePromoEligibilityRules("edit_promo");
            assert(!("source" in converted) && !("preset" in converted), "legacy display metadata never corrupts stored rule");
            assert(helpers.promoEligibilitySummary(converted, "first_purchase").includes("Guest atau member"), "legacy definition remains explicit");

            // 10. Simulasi memilih diagnostic evaluator yang sama dan menghasilkan baris per kondisi.
            const simulation = {
                eligible: false,
                reason: "Umur akun melewati batas promo.",
                diagnostics: {
                    eligibility: {
                        source: "explicit",
                        operator: "all",
                        eligible: false,
                        conditions: [
                            { field: "authentication_status", operator: "equals", expected: "member", actual: "member", matched: true, message: "Syarat terpenuhi." },
                            { field: "account_age_days", operator: "less_than_or_equal", expected: 7, actual: 10, matched: false, message: "Umur akun melewati batas promo." },
                        ],
                    },
                    identity: { status: "member_active", is_authenticated: true, account_active: true },
                    history: { status: "new", success_order_count: 0, has_success_order: false },
                    specific_target: { required: false },
                },
            };
            assert(helpers.promoEligibilitySimulationConditions(simulation) === simulation.diagnostics.eligibility.conditions, "eligibility diagnostics take precedence");
            const conditionRows = helpers.promoEligibilityDiagnosticRows(simulation);
            assert(conditionRows.filter((row) => row.label.startsWith("Syarat ")).length === 2, "one rendered row per condition");
            const ageRow = conditionRows.find((row) => row.label.includes("Umur akun"));
            assert(ageRow.value.includes("Tidak terpenuhi") && ageRow.value.includes("Aktual: 10 hari"), "failed condition explains safe actual value");
            const combinedRows = helpers.promoSimulationDiagnosticRows(simulation);
            assert(combinedRows.some((row) => row.label === "Pola syarat" && row.value.includes("SEMUA")), "group operator rendered in Indonesian");
            assert(combinedRows.some((row) => row.label === "Histori transaksi" && row.value.includes("Pembeli pertama")), "history label is unambiguous");
            ["authentication_status", "account_age_days", "less_than_or_equal", "explicit"]
                .forEach((raw) => assert(!JSON.stringify(combinedRows).includes(raw), `diagnostic hides raw enum: ${raw}`));
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
            self.fail(
                "Node eligibility edit/diagnostic test failed\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

        self.assertIn("populatePromoEligibility(prefix, data);", dashboard_js)
        self.assertIn("Aturan legacy yang dikonversi untuk tampilan", dashboard_js)
        self.assertIn('state.source !== "legacy_adapter"', dashboard_js)
        self.assertIn("promoEligibilitySimulationChecksMarkup(data)", dashboard_js)

    def test_customer_segment_helpers_selector_and_simulation_contract(self):
        root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            r"""
            global.window = {
                location: { href: "", origin: "http://localhost" },
                addEventListener: () => {},
            };
            global.localStorage = { getItem: () => "token", removeItem: () => {} };
            global.document = {
                getElementById: () => null,
                addEventListener: () => {},
                querySelector: () => null,
                querySelectorAll: () => [],
                activeElement: null,
            };
            global.bootstrap = { Modal: function Modal() { return { show() {}, hide() {} }; } };
            global.confirm = () => true;

            require(process.cwd() + "/web/js/dashboard.js");
            const helpers = window.__lixafaPromoValidation;
            function assert(condition, message) {
                if (!condition) throw new Error(message);
            }

            assert(helpers.canonicalPromoCustomerSegment(" all_customers ") === "all", "legacy all alias");
            const labels = {
                all: "Semua pelanggan",
                members_only: "Khusus member terdaftar",
                guests_only: "Khusus guest",
                new: "Pembeli pertama",
                existing: "Pelanggan lama",
            };
            Object.entries(labels).forEach(([value, label]) => {
                assert(helpers.promoCustomerSegmentDisplay(value) === label, `human label for ${value}`);
                assert(!helpers.promoCustomerSegmentDisplay(value).includes(value), `raw enum hidden for ${value}`);
            });
            assert(
                helpers.promoCustomerSegmentDisplay("specific", 5) === "Pelanggan tertentu: 5 pelanggan dipilih",
                "specific detail/review includes selected count",
            );

            const active = helpers.normalizePromoCustomerOption({
                customer_id: "12", customer_name: "Ayu", phone: "0812", account_active: 1,
            });
            const inactive = helpers.normalizePromoCustomerOption({
                id: 18, name: "Bima", customer_phone: "+62813", active: 0,
            });
            assert(active.id === 12 && active.active === true && active.name === "Ayu", "active customer option");
            assert(inactive.id === 18 && inactive.active === false && inactive.phone === "+62813", "inactive option retained for simulation");
            assert(
                helpers.filterPromoCustomerOptions([active, inactive, { id: 0 }], "bima").map((item) => item.id).join(",") === "18",
                "customer selector searches canonical account rows",
            );

            const guest = helpers.buildPromoSimulationIdentity("guest", "12", " 081234 ", " game-1 ");
            assert(guest.customer_id === null, "guest simulation cannot forge member id");
            assert(guest.customer_phone === "081234" && guest.target_id === "game-1", "guest identity payload");
            const member = helpers.buildPromoSimulationIdentity("member", "12", " +6281234 ", "target-2");
            assert(member.customer_id === 12, "member identity sends canonical account id");
            assert(member.customer_phone === "+6281234" && member.target_id === "target-2", "member identity context");

            const diagnostics = helpers.promoSimulationDiagnosticRows({
                reason_code: "CUSTOMER_NOT_TARGETED",
                diagnostics: {
                    customer_segment: "specific",
                    segment_match: false,
                    identity: { status: "member_inactive", is_authenticated: true, account_active: false },
                    history: { status: "existing", success_order_count: 2, has_success_order: true },
                    specific_target: { required: true, matched: false, selected_count: 3 },
                },
            });
            const byLabel = Object.fromEntries(diagnostics.map((item) => [item.label, item.value]));
            assert(byLabel["Segmentasi"] === "Pelanggan tertentu: 3 pelanggan dipilih", "diagnostic segment label");
            assert(byLabel["Kecocokan segment"] === "Tidak cocok", "diagnostic segment mismatch");
            assert(byLabel["Status login"].includes("akun nonaktif"), "diagnostic inactive account");
            assert(byLabel["Histori transaksi"].includes("2 transaksi SUCCESS"), "diagnostic authoritative history");
            assert(byLabel["Target khusus"].includes("Tidak cocok"), "diagnostic specific target mismatch");
            assert(byLabel["Kode alasan"] === "CUSTOMER_NOT_TARGETED", "diagnostic reason code");
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
            self.fail(f"Node segment helper test failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")

    def test_customer_segment_static_ui_and_cache_busters(self):
        root = Path(__file__).resolve().parents[1]
        dashboard_js = (root / "web" / "js" / "dashboard.js").read_text(encoding="utf-8")
        dashboard_html = (root / "web" / "admin-dashboard.html").read_text(encoding="utf-8")
        storefront_html = (root / "web" / "index.html").read_text(encoding="utf-8")

        defaults = dashboard_js.split("const PROMO_CUSTOMER_SEGMENT_DEFAULTS = [", 1)[1].split("];", 1)[0]
        expected = [
            ("all", "Semua pelanggan"),
            ("members_only", "Khusus member terdaftar"),
            ("guests_only", "Khusus guest"),
            ("new", "Pembeli pertama"),
            ("existing", "Pelanggan lama"),
            ("specific", "Pelanggan tertentu"),
        ]
        positions = []
        for value, label in expected:
            marker = f'{{ value: "{value}", label: "{label}"'
            self.assertIn(marker, defaults)
            positions.append(defaults.index(marker))
        self.assertEqual(positions, sorted(positions), "Enam opsi harus mengikuti urutan kontrak backend")
        for helper in (
            "Promo hanya dapat digunakan oleh pelanggan yang sudah masuk ke akun aktif.",
            "Promo hanya dapat digunakan tanpa login akun.",
            "Guest atau member yang belum pernah memiliki transaksi berhasil.",
            "Guest atau member yang sudah pernah memiliki minimal satu transaksi berhasil.",
        ):
            self.assertIn(helper, defaults)

        self.assertIn('type="search" id="${prefix}_customer_search"', dashboard_js)
        self.assertIn('type="hidden" id="${prefix}_customer_ids"', dashboard_js)
        self.assertIn('data-promo-customer-target hidden', dashboard_js)
        self.assertIn('node.hidden = !customerTargetRequired', dashboard_js)
        self.assertIn('clearPromoCustomerSelections(prefix)', dashboard_js)
        self.assertIn('setPromoCustomerSelections(prefix, selectedCustomerTargets)', dashboard_js)
        self.assertIn('hydratePromoCustomerSelections(prefix, selectedCustomerTargets)', dashboard_js)
        self.assertIn('/admin/api/promos/customer-options?', dashboard_js)
        self.assertIn('void searchPromoCustomers(prefix, query)', dashboard_js)
        self.assertIn('}, 250);', dashboard_js)
        self.assertNotIn('const legacyCustomers = await api("/admin/api/customers")', dashboard_js)
        self.assertNotIn('placeholder="Contoh: 12, 18, 25"', dashboard_js)
        self.assertIn('id="promo_simulation_identity_mode"', dashboard_html)
        self.assertIn('id="promo_simulation_customer_id"', dashboard_html)
        self.assertIn('id="edit_promo_simulation_identity_mode"', dashboard_html)
        self.assertIn('id="edit_promo_simulation_customer_id"', dashboard_html)
        self.assertIn('/web/css/admin.css?v=14', dashboard_html)
        self.assertIn('/web/js/dashboard.js?v=promo-eligibility-20260720-1', dashboard_html)
        self.assertIn('/web/js/topup.js?v=33', storefront_html)

    def test_promo_save_uses_json_and_dashboard_has_unique_ids(self):
        root = Path(__file__).resolve().parents[1]
        dashboard_js = (root / "web" / "js" / "dashboard.js").read_text(encoding="utf-8")
        dashboard_html = (root / "web" / "admin-dashboard.html").read_text(encoding="utf-8")

        self.assertIn(
            'api("/admin/api/promos", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(getPromoPayload("promo")) })',
            dashboard_js,
        )
        self.assertIn(
            'headers: { "Content-Type": "application/json" }, body: JSON.stringify(getPromoPayload("edit_promo"))',
            dashboard_js,
        )
        ids = []
        marker = 'id="'
        for fragment in dashboard_html.split(marker)[1:]:
            ids.append(fragment.split('"', 1)[0])
        duplicates = sorted({element_id for element_id in ids if ids.count(element_id) > 1})
        self.assertEqual(duplicates, [], f"Duplicate HTML ids: {duplicates}")
        dynamic_suffixes = {
            fragment.split('"', 1)[0]
            for fragment in dashboard_js.split('id="${prefix}_')[1:]
        }
        runtime_collisions = sorted(
            element_id
            for suffix in dynamic_suffixes
            for element_id in (f"promo_{suffix}", f"edit_promo_{suffix}")
            if element_id in ids
        )
        self.assertEqual(runtime_collisions, [], f"Runtime promo IDs collide with base HTML: {runtime_collisions}")
        self.assertIn('<option value="banner">Konten banner</option>', dashboard_js)
        self.assertIn('<option value="payment_method">Promo metode pembayaran</option>', dashboard_js)
        self.assertIn('/web/js/dashboard.js?v=promo-eligibility-20260720-1', dashboard_html)


if __name__ == "__main__":
    unittest.main()
