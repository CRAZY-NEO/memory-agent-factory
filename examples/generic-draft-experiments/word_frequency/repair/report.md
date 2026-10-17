# 候选组件报告

需求：生成英文词频统计组件，严格使用 execute(payload) -> dict。输入为 {"text": 字符串}，输出仅为 {"frequencies": [{"word": 字符串, "count": 正整数}]}。单词严格指连续 ASCII 英文字母 [A-Za-z]+，统一小写；数字、中文、撇号、连字符、下划线和其他非 ASCII 字母字符均作为分隔符。先按次数降序排列，同频按小写单词字母顺序排列。空文本或没有英文单词时返回空 frequencies 列表，不修改输入。只使用允许的纯 Python 标准库，不需要训练、分类或检索。

状态：draft

此候选不进入已验证组件目录，也不会替换正在使用的版本。
基础接口、安全和执行检查：True；提议示例一致性：True；独立业务验证：尚未提供。模型提议用例不等于可信评估。

失败用例与差异：
[]

{
  "title": "英文词频统计组件（仅 ASCII 英文字母分词）",
  "search_queries": [
    "python word frequency counter regex findall lowercase sorted",
    "pure python word frequency count regex alphabetical sort"
  ],
  "input_schema": {
    "type": "object",
    "properties": {
      "text": {
        "type": "string"
      }
    },
    "required": [
      "text"
    ],
    "additionalProperties": false
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "frequencies": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "word": {
              "type": "string"
            },
            "count": {
              "type": "integer",
              "minimum": 1
            }
          },
          "required": [
            "word",
            "count"
          ],
          "additionalProperties": false
        }
      }
    },
    "required": [
      "frequencies"
    ],
    "additionalProperties": false
  },
  "assumptions": [
    "调用方传入的 payload 是 JSON 对象，且 text 字段存在并严格为字符串；不符合时组件会抛出异常。",
    "词频统计只识别连续 ASCII 英文字母 [A-Za-z]+，统一转为小写后计数；数字、中文、撇号、连字符、下划线、空白及其他非 ASCII 字母字符都作为分隔符。",
    "输出按词频降序排列；同频时按小写单词的字母顺序升序排列。",
    "空文本或文本中没有 ASCII 英文字母时，返回空的 frequencies 列表。",
    "组件返回新构造的 JSON 对象，不会修改输入的 payload 或其中的 text。"
  ],
  "limitations": [
    "不把带重音或其他非 ASCII 字母的字符视为单词字符，因此法语、德语等 Unicode 字母会被拆分或忽略。",
    "不处理撇号、连字符、下划线等构成的缩略词或复合词，例如 hello-world 会被拆成 hello 和 world。",
    "不提供停用词过滤、词形还原、大小写归一化以外的自然语言处理能力。",
    "基于内存一次性处理，极大文本可能导致较高内存占用；未针对流式或分布式场景优化。"
  ],
  "examples": [
    {
      "input": {
        "text": "Hello, world! Hello there."
      },
      "expected": {
        "frequencies": [
          {
            "word": "hello",
            "count": 2
          },
          {
            "word": "there",
            "count": 1
          },
          {
            "word": "world",
            "count": 1
          }
        ]
      }
    },
    {
      "input": {
        "text": "Apple banana apple; APPLE, Banana!"
      },
      "expected": {
        "frequencies": [
          {
            "word": "apple",
            "count": 3
          },
          {
            "word": "banana",
            "count": 2
          }
        ]
      }
    },
    {
      "input": {
        "text": "dog cat dog bird cat"
      },
      "expected": {
        "frequencies": [
          {
            "word": "cat",
            "count": 2
          },
          {
            "word": "dog",
            "count": 2
          },
          {
            "word": "bird",
            "count": 1
          }
        ]
      }
    },
    {
      "input": {
        "text": "123456 中文テスト _-"
      },
      "expected": {
        "frequencies": []
      }
    }
  ]
}