def pytest_addoption(parser):  # type: ignore[no-untyped-def]
    parser.addoption(
        "--run-evals",
        action="store_true",
        default=False,
        help="Run live LLM evals (costs money, requires ANTHROPIC_API_KEY).",
    )
