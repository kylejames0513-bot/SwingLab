# CaddieInsight — the handful of commands that are easy to get wrong by hand.
#
# The app deploys itself: merging to `main` is deploying, via Railway. The
# storefront does not — it is uploaded by hand, so `theme-zip` builds the
# artifact that actually ships and `dist/UPLOAD.md` carries the preview
# checklist that decides whether the release is good.

.DEFAULT_GOAL := help
.PHONY: help theme-zip theme-check test test-fast parity brand

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

theme-zip:  ## Build dist/caddieinsight-theme.zip for manual Shopify upload
	python3 scripts/package_theme.py

theme-check:  ## Run Shopify's theme linter (needs the Shopify CLI)
	shopify theme check --path storefront-theme --fail-level warning

parity:  ## The contracts that keep the storefront and the app one instrument
	python3 -m pytest tests/test_app_storefront_parity.py \
		tests/test_theme_package.py tests/test_crawler_surface.py -q

test-fast:  ## The suite without the ffmpeg/browser integration tests
	# test_pwa_shell is ignored too: its importorskip catches a missing
	# playwright PACKAGE but not a missing browser BINARY, so on a machine
	# without Chromium it fails rather than skips. CI installs the browser
	# and runs it; this target must be green out of the box.
	python3 -m pytest tests -q \
		--ignore=tests/test_integration_ffmpeg.py \
		--ignore=tests/test_guided_report_browser.py \
		--ignore=tests/test_pwa_shell.py

test:  ## The whole suite
	python3 -m pytest tests -q

brand:  ## Regenerate the brand marks and PWA icons from one geometry
	python3 store-assets/make_brand.py
