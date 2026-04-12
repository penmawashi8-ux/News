# Claude Code 開発メモ

## ブランチ運用ルール

- **全ての変更は `main` ブランチに直接マージすること**
- フィーチャーブランチで作業した場合も、完了後は必ず `main` へマージして push する

```bash
# 作業完了後のマージ手順
git checkout main
git merge <ブランチ名>
git push origin main
```
