# mdview 渲染测试 / GFM features

一段普通文本，包含 **粗体**、*斜体*、`行内代码`、~~删除线~~、[链接](https://github.com/github/cmark-gfm)
和自动链接 https://example.com 。

## 表格与任务列表

| 特性 | cmark-gfm | 说明 |
|:-----|:---------:|-----:|
| 表格 | ✅ | GFM 扩展 |
| 任务列表 | ✅ | GFM 扩展 |
| 脚注 | ✅ | GFM 扩展 |

- [x] 已完成的项
- [ ] 待办项
  - 嵌套项

## 代码块

```python
def hello(name: str) -> str:
    return f"hello, {name}"
```

```
无语言标注的代码块
```

## 引用与原生 HTML

> 引用块
>> 嵌套引用

<details>
<summary>折叠块（GitHub 上可点开）</summary>

里面的内容，含 <sub>下标</sub> 和 <kbd>Ctrl</kbd>。

</details>

## 图片与相对路径

下面这张图用的是相对路径，靠 `<base>` 标签解析：

![示例图片](sample.png)

脚注示例[^1]。

[^1]: 这是脚注内容，GFM 扩展之一。
