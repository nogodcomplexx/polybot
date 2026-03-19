"""
Sentiment analysis engine — FinBERT with VADER fallback.
"""

import logging
from typing import List, Optional

from src.data.models import NewsArticle, SentimentResult

logger = logging.getLogger(__name__)

# Lazy-loaded models to avoid slow startup
_finbert_pipeline = None
_vader_analyzer = None
_vader_load_failed = False


def _load_finbert():
    """Lazy-load FinBERT sentiment pipeline."""
    global _finbert_pipeline
    if _finbert_pipeline is None:
        try:
            from transformers import pipeline
            logger.info("Loading FinBERT model (first time may download ~500MB)...")
            _finbert_pipeline = pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
                top_k=None,
                truncation=True,
                max_length=512,
            )
            logger.info("FinBERT loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load FinBERT: {e}")
            _finbert_pipeline = None
    return _finbert_pipeline


def _load_vader():
    """Lazy-load VADER sentiment analyzer."""
    global _vader_analyzer
    global _vader_load_failed
    if _vader_analyzer is None:
        if _vader_load_failed:
            return None
        try:
            import nltk
            nltk.download("vader_lexicon", quiet=True)
            from nltk.sentiment import SentimentIntensityAnalyzer
            _vader_analyzer = SentimentIntensityAnalyzer()
            logger.info("VADER loaded successfully")
        except Exception as e:
            _vader_load_failed = True
            logger.error(f"Failed to load VADER (install 'nltk' to enable): {e}")
    return _vader_analyzer


class SentimentAnalyzer:
    """Analyze sentiment of news text using FinBERT (primary) or VADER (fallback)."""

    def __init__(self, model: str = "finbert"):
        self.model_name = model

    def analyze(self, text: str) -> SentimentResult:
        """Analyze sentiment of a single text."""
        if not text or len(text.strip()) < 10:
            return SentimentResult(text=text, label="neutral", score=0.0, confidence=0.0)

        if self.model_name == "finbert":
            result = self._analyze_finbert(text)
            if result:
                return result
            # Fallback to VADER
            logger.debug("FinBERT unavailable, falling back to VADER")

        return self._analyze_vader(text)

    async def analyze_articles(self, articles: List[NewsArticle]) -> List[SentimentResult]:
        """Analyze sentiment for a list of articles (non-blocking)."""
        import asyncio
        
        def _sync_analyze():
            results = []
            for article in articles:
                # Combine title + description for analysis
                text = f"{article.title}. {article.description}".strip()
                result = self.analyze(text)
                results.append(result)
            return results

        # Run CPU-bound analysis in a separate thread to avoid blocking the event loop
        return await asyncio.to_thread(_sync_analyze)

    def aggregate_sentiment(self, results: List[SentimentResult]) -> SentimentResult:
        """Aggregate multiple sentiment results into one."""
        if not results:
            return SentimentResult(text="", label="neutral", score=0.0, confidence=0.0)

        # Weighted average by confidence
        total_weight = sum(r.confidence for r in results) or 1.0
        avg_score = sum(r.score * r.confidence for r in results) / total_weight
        avg_confidence = sum(r.confidence for r in results) / len(results)

        if avg_score > 0.1:
            label = "positive"
        elif avg_score < -0.1:
            label = "negative"
        else:
            label = "neutral"

        # Collect key phrases
        all_phrases = []
        for r in results:
            all_phrases.extend(r.key_phrases)

        return SentimentResult(
            text=f"Aggregated from {len(results)} sources",
            score=avg_score,
            confidence=avg_confidence,
            label=label,
            key_phrases=list(set(all_phrases))[:10],
            model_used=self.model_name,
        )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _analyze_finbert(self, text: str) -> Optional[SentimentResult]:
        """Analyze using FinBERT."""
        pipeline = _load_finbert()
        if not pipeline:
            return None

        try:
            # Truncate to 512 chars for FinBERT
            truncated = text[:512]
            outputs = pipeline(truncated)

            # outputs is a list of lists of dicts: [[{label, score}, ...]]
            if outputs and isinstance(outputs[0], list):
                preds = outputs[0]
            else:
                preds = outputs

            # Find the dominant sentiment
            best = max(preds, key=lambda x: x["score"])
            label = best["label"].lower()
            confidence = best["score"]

            # Map to unified score: positive=+1, negative=-1, neutral=0
            score_map = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
            score = score_map.get(label, 0.0) * confidence

            return SentimentResult(
                text=text,
                score=score,
                confidence=confidence,
                label=label,
                model_used="finbert",
            )
        except Exception as e:
            logger.error(f"FinBERT analysis error: {e}")
            return None

    def _analyze_vader(self, text: str) -> SentimentResult:
        """Analyze using VADER (fallback)."""
        analyzer = _load_vader()
        if not analyzer:
            return SentimentResult(text=text, label="neutral", score=0.0, confidence=0.0)

        try:
            scores = analyzer.polarity_scores(text)
            compound = scores["compound"]

            if compound >= 0.05:
                label = "positive"
            elif compound <= -0.05:
                label = "negative"
            else:
                label = "neutral"

            confidence = abs(compound)

            return SentimentResult(
                text=text,
                score=compound,
                confidence=min(confidence, 1.0),
                label=label,
                model_used="vader",
            )
        except Exception as e:
            logger.error(f"VADER analysis error: {e}")
            return SentimentResult(text=text, label="neutral", score=0.0, confidence=0.0)
