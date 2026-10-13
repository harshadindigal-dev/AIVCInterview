.PHONY: up down test eval record-cache reset-db logs

up:            ## build and start on http://localhost:8000 (seeds on first start)
	docker compose up --build

down:          ## stop (keeps the database volume)
	docker compose down

test:          ## run the test suite in the image (the live e2e test needs OPENROUTER_API_KEY)
	docker compose build -q
	docker compose run --rm --no-deps -e DATABASE_URL=sqlite:////tmp/test.db app pytest -q -p no:cacheprovider

eval:          ## evaluate TRIAGE_MODEL + EVAL_MODELS; writes eval_results/ (~$3, real API calls)
	docker compose build -q
	docker compose run --rm --no-deps -v "$(CURDIR)/eval_results:/app/eval_results" app python -m scripts.run_eval

record-cache:  ## not applicable: this build has no mock mode (DECISIONS.md D1)
	@echo "No recorded cache in the live-only build. Use 'make eval' to refresh eval_results/."

reset-db:      ## delete the database volume; next 'make up' re-seeds (~$2)
	docker compose down -v

logs:
	docker compose logs -f app
