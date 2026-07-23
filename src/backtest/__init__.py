"""Offline backtesting framework — local, free, simulated money only.

Everything in this package runs as a standalone process on the local
machine: data comes from yfinance (cached to parquet on disk), execution
goes through an in-memory :class:`~src.backtest.broker.SimulatedBroker`,
and results are written to local HTML reports. No StockArena, no Redis,
no PostgreSQL, no paid APIs.
"""
