import { BarChart3, Bot, CheckSquare, ClipboardList, LayoutDashboard, MessagesSquare } from "lucide-react";

export const navigationItems = [
  { key: "dashboard", path: "/app", icon: LayoutDashboard },
  { key: "inbox", path: "/app/inbox", icon: MessagesSquare },
  { key: "workforce", path: "/app/workforce", icon: Bot },
  { key: "workItems", path: "/app/work-items", icon: ClipboardList },
  { key: "approvals", path: "/app/approvals", icon: CheckSquare },
  { key: "analytics", path: "/app/analytics", icon: BarChart3 }
] as const;
