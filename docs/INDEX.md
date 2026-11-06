# MaxCameraApp documentation

## Пользователю

- [Полное руководство на русском, PDF](MaxCameraApp_User_Guide_RU.pdf)
- [Редактируемое руководство](MaxCameraApp_User_Guide_RU.md)
- [README и quick start](../README.md)

## Оператору и разработчику

- [Architecture / API](ARCHITECTURE.md)
- [Security / privacy](SECURITY.md)
- [Deployment / services / remote access](DEPLOYMENT.md)
- [Development / dependencies / tests](DEVELOPMENT.md)
- [Publication verification and exclusions](PUBLICATION_VERIFICATION.md)

## Реализованный AI-контракт и прошлые проверки

- [AI GUI update report](AI_UPDATE_REPORT.md)
- [AI visual overlays](AI_VISUALS.md)
- [Pinned upstream camera contract](../backend/data/upstream.json)

## Иллюстрации

[Desktop](images/screenshot-ai-desktop.png) и [mobile](images/screenshot-ai-mobile.png) показывают настоящий UI на одноразовом синтетическом стенде. Сцена, имена, детекции и результаты вымышлены. Переключатель Synthetic test отсутствует в production. Это не реальные кадры и не квалификация точности моделей.

## Обновление документации

Сначала сверяйте интерфейс, policy и XML. Меняйте Markdown-исходник руководства, пересобирайте PDF и проверяйте все страницы после layout-правок. Не включайте render/QA intermediates, локальные аккаунты, сцены, gallery или веса в публикацию.
