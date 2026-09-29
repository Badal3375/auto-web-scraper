"""AutoScraper: config-driven, end-to-end web scraping pipeline."""
__version__ = "1.0.0"

from .analyzer import AnalysisResult, WebsiteAnalyzer

__all__ = ["AnalysisResult", "WebsiteAnalyzer", "__version__"]

