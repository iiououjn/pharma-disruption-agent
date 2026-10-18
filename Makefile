export PYTHONPATH := src
test:
	python -m unittest discover -s tests -v
demo:
	python -m pdsia.cli demo
eval:
	python -m pdsia.cli eval
fixtures:
	python scripts/build_fixtures.py
api:
	uvicorn pdsia.api:app --reload
ui:
	streamlit run app/streamlit_app.py
