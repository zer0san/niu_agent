"""
Text Summarizer Skill - 文本摘要提取

功能：
- 提取关键句子
- 统计词频
- 生成摘要
- 支持中英文
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Optional

import jieba

from skills.exceptions import (
    SkillError,
    InputTypeError,
    InputValueError,
    ParseError,
)
from skills.error_utils import (
    make_error_result,
    make_success_result,
    validate_type,
    validate_not_empty_string,
    validate_max_length,
    validate_positive_integer,
    measure_latency,
)


# ==================================================================================
# 配置
# ==================================================================================

# 最大文本长度
MAX_TEXT_LENGTH = 100000

# 默认摘要句子数
DEFAULT_MAX_SENTENCES = 3

# 最大关键词数
DEFAULT_MAX_KEYWORDS = 10

# 中文句子结束符
CHINESE_SENTENCE_ENDINGS = {'。', '！', '？', '；', '…'}

# 英文句子结束符
ENGLISH_SENTENCE_ENDINGS = {'.', '!', '?', ';'}

# 停用词（从文件加载）
def _load_stopwords() -> set[str]:
    """从stopwords_baidu.txt加载停用词"""
    stopwords_file = Path(__file__).parent / 'stopwords_baidu.txt'
    if stopwords_file.exists():
        words = set()
        with open(stopwords_file, 'r', encoding='utf-8') as f:
            for line in f:
                word = line.strip()
                if word:  # 跳过空行
                    words.add(word)
        return words
    # 如果文件不存在，返回空集合
    return set()

STOP_WORDS = _load_stopwords()


# ==================================================================================
# 文本处理函数
# ==================================================================================

def _detect_language(text: str) -> str:
    """
    检测文本语言

    Args:
        text: 输入文本

    Returns:
        'zh' (中文) 或 'en' (英文)
    """
    # 统计中文字符数
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    # 统计英文字符数
    english_chars = len(re.findall(r'[a-zA-Z]', text))

    if chinese_chars > english_chars * 0.5:
        return 'zh'
    return 'en'


def _split_sentences(text: str) -> list[str]:
    """
    分割句子

    Args:
        text: 输入文本

    Returns:
        句子列表
    """
    # 合并所有句子结束符
    all_endings = CHINESE_SENTENCE_ENDINGS | ENGLISH_SENTENCE_ENDINGS

    # 构建分割正则
    pattern = '[' + re.escape(''.join(all_endings)) + ']'

    # 分割句子
    sentences = re.split(pattern, text)

    # 清理空句子
    sentences = [s.strip() for s in sentences if s.strip()]

    return sentences


def _extract_words(text: str, language: str) -> list[str]:
    """
    提取单词/词语

    Args:
        text: 输入文本
        language: 语言 ('zh' 或 'en')

    Returns:
        词语列表
    """
    if language == 'zh':
        # 中文：使用jieba精确分词
        words = jieba.lcut(text)
        # 过滤单字和空白
        words = [w.strip() for w in words if len(w.strip()) > 1]
    else:
        # 英文：按空格分割
        words = re.findall(r'[a-zA-Z]+', text.lower())

    # 过滤停用词
    words = [w for w in words if w.lower() not in STOP_WORDS]

    return words


def _calculate_word_frequency(words: list[str]) -> dict[str, int]:
    """
    计算词频

    Args:
        words: 词语列表

    Returns:
        词频字典
    """
    return dict(Counter(words))


def _calculate_sentence_score(sentence: str, word_freq: dict[str, int], language: str) -> float:
    """
    计算句子得分

    Args:
        sentence: 句子
        word_freq: 词频字典
        language: 语言

    Returns:
        句子得分
    """
    words = _extract_words(sentence, language)
    if not words:
        return 0.0

    # 计算句子中词的总频率
    score = sum(word_freq.get(w, 0) for w in words)

    # 归一化（除以句子长度）
    score = score / len(words) if words else 0

    return score


def _extract_keywords(text: str, max_keywords: int = DEFAULT_MAX_KEYWORDS) -> list[dict[str, any]]:
    """
    提取关键词

    Args:
        text: 输入文本
        max_keywords: 最大关键词数

    Returns:
        关键词列表 [{'word': str, 'count': int, 'frequency': float}]
    """
    language = _detect_language(text)
    words = _extract_words(text, language)
    word_freq = _calculate_word_frequency(words)

    # 按频率排序
    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)

    # 计算总词数
    total_words = len(words) if words else 1

    # 返回前N个关键词
    keywords = []
    for word, count in sorted_words[:max_keywords]:
        keywords.append({
            'word': word,
            'count': count,
            'frequency': round(count / total_words, 4),
        })

    return keywords


# ==================================================================================
# 主函数
# ==================================================================================

def text_summarizer(
    text: str,
    max_sentences: int = DEFAULT_MAX_SENTENCES,
    max_keywords: int = DEFAULT_MAX_KEYWORDS,
    extract_keywords: bool = True,
) -> dict:
    """
    文本摘要提取

    Args:
        text: 输入文本
        max_sentences: 最大摘要句子数
        max_keywords: 最大关键词数
        extract_keywords: 是否提取关键词

    Returns:
        包含摘要结果或错误的字典

    Examples:
        >>> text_summarizer("这是一段测试文本。它包含多个句子。每个句子都有不同的内容。", max_sentences=2)
        {'skill_name': 'text_summarizer', 'status': 'success', 'output': {'summary': '...', 'key_sentences': [...], ...}, ...}
    """
    # 安全地处理输入数据
    text_preview = text[:100] + '...' if isinstance(text, str) and len(text) > 100 else text
    input_data = {
        'text': text_preview,
        'max_sentences': max_sentences,
        'max_keywords': max_keywords,
        'extract_keywords': extract_keywords,
    }

    try:
        with measure_latency() as timer:
            # 验证文本不为空
            validate_not_empty_string(
                text, 'text', 'text_summarizer', 'SUMM-VAL-001'
            )

            # 验证文本长度
            validate_max_length(
                text, MAX_TEXT_LENGTH, 'text', 'text_summarizer', 'SUMM-VAL-002'
            )

            # 验证max_sentences
            validate_positive_integer(
                max_sentences, 'max_sentences', 'text_summarizer', 'SUMM-VAL-003'
            )

            # 验证max_keywords
            if not isinstance(max_keywords, int) or max_keywords < 1 or max_keywords > 50:
                raise InputValueError(
                    code='SUMM-VAL-004',
                    message='max_keywords必须是1-50之间的整数',
                    details={'max_keywords': max_keywords},
                    suggestion='请将max_keywords设置为1-50之间的整数'
                )

            # 检测语言
            language = _detect_language(text)

            # 分割句子
            sentences = _split_sentences(text)

            if not sentences:
                raise ParseError(
                    code='SUMM-EXEC-001',
                    message='无法从文本中提取句子',
                    details={'text_length': len(text)},
                    suggestion='请确保文本包含完整的句子'
                )

            # 提取词语
            words = _extract_words(text, language)

            # 计算词频
            word_freq = _calculate_word_frequency(words)

            # 计算每个句子的得分
            sentence_scores = []
            for i, sentence in enumerate(sentences):
                score = _calculate_sentence_score(sentence, word_freq, language)
                sentence_scores.append({
                    'index': i,
                    'sentence': sentence,
                    'score': score,
                })

            # 按得分排序，选择前N个句子
            sorted_sentences = sorted(sentence_scores, key=lambda x: x['score'], reverse=True)
            selected_sentences = sorted_sentences[:max_sentences]

            # 按原始顺序排序
            selected_sentences.sort(key=lambda x: x['index'])

            # 生成摘要
            summary = ''.join(s['sentence'] for s in selected_sentences)

            # 提取关键句子
            key_sentences = [s['sentence'] for s in selected_sentences]

            # 提取关键词
            keywords = _extract_keywords(text, max_keywords) if extract_keywords else []

            # 统计信息
            stats = {
                'total_chars': len(text),
                'total_sentences': len(sentences),
                'total_words': len(words),
                'unique_words': len(word_freq),
                'language': language,
                'compression_ratio': round(len(summary) / len(text), 4) if text else 0,
            }

            # 构建输出
            output = {
                'summary': summary,
                'key_sentences': key_sentences,
                'keywords': keywords,
                'stats': stats,
            }

        return make_success_result(
            'text_summarizer',
            input_data,
            output,
            timer.elapsed_ms
        )

    except SkillError as exc:
        return make_error_result('text_summarizer', exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)

    except Exception as exc:
        return make_error_result('text_summarizer', exc, input_data, timer.elapsed_ms if 'timer' in dir() else 0.0)
