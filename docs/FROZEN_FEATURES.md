# Frozen Features

This project is currently focused on two workflows:

- Threads engagement posts that lead readers to the Telegram group.
- Telegram daily affiliate link catalog by date and category.

The following features are frozen by default. They are not registered in the Telegram bot command list and should not run schedulers, call external APIs, or spend AI quota unless explicitly re-enabled later.

| Area | Commands/modules | Flag | Reason |
| --- | --- | --- | --- |
| Threads demand scanner | `/scanthreads`, `app/services/threads_demand_scanner.py` | `ENABLE_DEMAND_SCANNER=false` | Requires keyword-search permission and adds operational complexity. |
| Purchase intent workflow | `/buyops`, `/buyop`, `/approvebuy`, `/replybuy`, `app/services/demand_opportunity_service.py` | `ENABLE_PURCHASE_INTENT=false`, `ENABLE_OPPORTUNITY_QUEUE=false` | Replaced by Telegram daily catalog. |
| Manual demand intake | `/adddemand`, `/adddemandtext`, `/importdemands`, API demand intake | `ENABLE_MANUAL_DEMAND_INTAKE=false` | Not part of the simplified product loop. |
| Reply suggestions | `/replysuggestions`, reply analysis services | `ENABLE_COMMENT_GENERATOR=false` | No auto-reply workflow in the current scope. |
| Analytics sync | `/syncposts`, `/syncinsights`, `/syncreplies`, `/threadstats` | `ENABLE_THREADS_BACKGROUND_SYNC=false` | Avoid background Threads API calls. |
| Learning engines | `/learn`, `/autolearn`, account/persona/hook/CTA learning | `ENABLE_LEARNING_ENGINE=false` | Avoid extra state and AI/model churn. |
| Trend engines | `/trends`, `/trenddrafts`, Google Trends, marketplace intelligence | `ENABLE_TREND_ENGINE=false`, `ENABLE_GOOGLE_TRENDS=false` | Daily catalog and manual engagement are the active scope. |
| Cross-platform publishing | Reddit, X, Facebook, Instagram, browser intake | `ENABLE_CROSS_PLATFORM_PUBLISHER=false` | Not needed for Threads -> Telegram funnel. |

Frozen commands return a short message instead of crashing:

```text
Chuc nang nay hien dang duoc dong bang. Bot dang tap trung vao kho link Telegram va bai Threads dan ve group.
```

To re-enable a feature later, add a dedicated task that restores the command registration, scheduler, tests, and permission checks for that feature.
