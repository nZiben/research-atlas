# Research Atlas

Библиотека исследований AI: safety, интерпретируемость, эффективность, NLP, агенты, vision, robotics, RL, оценка и AI for Science.

**[Открыть сайт](https://nziben.github.io/research-atlas/)**

В десяти разделах — поисковый корпус из 1669 уникальных публикаций со ссылками на первоисточники. Поиск выполнен 8 ноября 2026 года. Это корпус для отбора. Опубликован первый концептуальный обзор: **[speculative decoding](https://nziben.github.io/research-atlas/#efficiency)** — 10 разобранных работ, краткое объяснение, подробный разбор и таблица условий. Остальные девять обзоров пока не опубликованы.

[Родственные проекты и ограничения](https://nziben.github.io/research-atlas/related.html).

Сайт статический, только для чтения. Поиск новых работ и подготовка обзоров выполняются локально владельцем; ключи и приватные материалы на сайт не попадают.

```bash
python3 -m review_forge serve --open
```

Для локального конвейера нужны Python 3.11+ и PDF-зависимость: `python3 -m pip install -e '.[pdf]'`.

[Настройка и команды](docs/local-setup.md) · [Метод обзора](docs/review-standard.md) · [Источники и охват](bibliography/README.md) · [Обновление сайта](docs/publication.md)
