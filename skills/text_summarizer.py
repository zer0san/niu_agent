"""Text Summarizer - 基于TF词频的抽取式文本摘要，支持中英文"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import jieba

from skills.exceptions import (
    InputValueError, ParseError, SkillError,
)
from skills.error_utils import (
    make_error_result, make_success_result, measure_latency,
    validate_not_empty_string, validate_max_length, validate_positive_integer,
)

# ---- 配置 ----
MAX_TEXT_LENGTH = 100000
DEFAULT_MAX_SENTENCES = 3
DEFAULT_MAX_KEYWORDS = 10
SENTENCE_ENDINGS = {'。', '！', '？', '；', '…', '.', '!', '?', ';'}

# 加载停用词
_stopwords_file = Path(__file__).parent / 'stopwords_baidu.txt'
STOP_WORDS = set(_stopwords_file.read_text(encoding='utf-8').strip().split('\n')) if _stopwords_file.exists() else set()


# ---- 文本处理 ----

def _detect_language(text: str) -> str:
    cn = len(re.findall(r'[一-鿿]', text))
    en = len(re.findall(r'[a-zA-Z]', text))
    return 'zh' if cn > en * 0.5 else 'en'


def _split_sentences(text: str) -> list[str]:
    pattern = '[' + re.escape(''.join(SENTENCE_ENDINGS)) + ']'
    return [s.strip() for s in re.split(pattern, text) if s.strip()]


def _extract_words(text: str, language: str) -> list[str]:
    if language == 'zh':
        words = jieba.lcut(text)
        words = [w.strip() for w in words if len(w.strip()) > 1]
    else:
        words = re.findall(r'[a-zA-Z]+', text.lower())
    return [w for w in words if w.lower() not in STOP_WORDS]


def _score_sentence(sentence: str, word_freq: dict[str, int], language: str) -> float:
    words = _extract_words(sentence, language)
    return sum(word_freq.get(w, 0) for w in words) / len(words) if words else 0.0


def _extract_keywords(text: str, max_n: int) -> list[dict]:
    language = _detect_language(text)
    words = _extract_words(text, language)
    freq = Counter(words)
    total = len(words) or 1
    return [
        {'word': w, 'count': c, 'frequency': round(c / total, 4)}
        for w, c in freq.most_common(max_n)
    ]


# ---- 主函数 ----

def text_summarizer(
    text: str,
    max_sentences: int = DEFAULT_MAX_SENTENCES,
    max_keywords: int = DEFAULT_MAX_KEYWORDS,
    extract_keywords: bool = True,
) -> dict:
    input_data = {'text': text[:100] + '...' if isinstance(text, str) and len(text) > 100 else text,
                  'max_sentences': max_sentences, 'max_keywords': max_keywords}

    try:
        with measure_latency() as timer:
            validate_not_empty_string(text, 'text', 'text_summarizer', 'SUMM-VAL-001')
            validate_max_length(text, MAX_TEXT_LENGTH, 'text', 'text_summarizer', 'SUMM-VAL-002')
            validate_positive_integer(max_sentences, 'max_sentences', 'text_summarizer', 'SUMM-VAL-003')

            if not (1 <= max_keywords <= 50):
                raise InputValueError(code='SUMM-VAL-004', message='max_keywords 需在 1-50 之间')

            language = _detect_language(text)
            sentences = _split_sentences(text)
            if not sentences:
                raise ParseError(code='SUMM-EXEC-001', message='无法提取句子，请确保文本包含完整句子',
                                 details={'text_length': len(text)})

            words = _extract_words(text, language)
            word_freq = Counter(words)

            scored = sorted(
                ({'index': i, 'sentence': s, 'score': _score_sentence(s, word_freq, language)}
                 for i, s in enumerate(sentences)),
                key=lambda x: x['score'], reverse=True
            )[:max_sentences]
            scored.sort(key=lambda x: x['index'])

            summary = ''.join(s['sentence'] for s in scored)
            keywords = _extract_keywords(text, max_keywords) if extract_keywords else []

            output = {
                'summary': summary,
                'key_sentences': [s['sentence'] for s in scored],
                'keywords': keywords,
                'stats': {
                    'total_chars': len(text),
                    'total_sentences': len(sentences),
                    'total_words': len(words),
                    'unique_words': len(word_freq),
                    'language': language,
                    'compression_ratio': round(len(summary) / len(text), 4) if text else 0,
                },
            }

        return make_success_result('text_summarizer', input_data, output, timer.elapsed_ms)

    except SkillError as exc:
        return make_error_result('text_summarizer', exc, input_data)
    except Exception as exc:
        return make_error_result('text_summarizer', exc, input_data)
