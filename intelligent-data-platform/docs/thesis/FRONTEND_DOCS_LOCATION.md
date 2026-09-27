# 前端改造依据文件位置（给 DSH）

本仓库存在多份本地副本。前端改造时，**只读取下列文件**，不要去搜其他 clone。

## 权威路径（桌面 authoring 副本）

工作根目录：

`C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform`

必读：

1. `docs/thesis/FRONTEND_UI_AUDIT.md` — UI 审计与改造方案（设计权威）
2. `docs/thesis/FRONTEND_IMPLEMENTATION_PROMPT.md` — 实施任务书（执行顺序与验收）
3. `docs/thesis/ui-audit-screenshots/` — 真实页面截图（01–08）

绝对路径：

```
C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform\docs\thesis\FRONTEND_UI_AUDIT.md
C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform\docs\thesis\FRONTEND_IMPLEMENTATION_PROMPT.md
C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform\docs\thesis\ui-audit-screenshots\
```

## 前端代码改动目录（同一副本）

只改：

```
C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform\services\web\
  index.html
  app.js
  styles.css
```

## 忽略

- `C:\Users\jsy28\dsh-workspace\...`（另一份临时 clone，勿作为作业目录）
- 不要新建 Quality 假页面、不要改后端
