from pathlib import Path


THEME_SECTION = (
    Path(__file__).parents[1]
    / "storefront-theme"
    / "sections"
    / "main-product.liquid"
)


def test_membership_selling_plans_are_scoped_to_the_selected_variant():
    source = THEME_SECTION.read_text(encoding="utf-8")

    assert "product.selling_plan_groups" not in source
    assert "current.selling_plan_allocations" in source
    assert "variant.selling_plan_allocations" in source
    assert 'data-sl-selling-plan-template="{{ variant.id }}"' in source
    assert "variant.sku == 'SL-PRO-LIFE'" in source


def test_variant_change_replaces_stale_selling_plan_inputs():
    source = THEME_SECTION.read_text(encoding="utf-8")

    assert "renderSellingPlans(opt.value);" in source
    assert "planContainer.replaceChildren();" in source
    assert (
        'planContainer.querySelectorAll(\'input[name="selling_plan"]\')'
        in source
    )
