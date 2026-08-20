import {
  BarChart3,
  Bot,
  CheckSquare,
  ClipboardList,
  Home,
  Inbox,
  Layers3,
  PlugZap,
  Settings,
  Tags,
  UsersRound
} from "lucide-react";

export const navigationItems = [
  { label: "Dashboard", path: "/app", icon: Home },
  { label: "Inbox", path: "/app/inbox", icon: Inbox },
  { label: "Workforce", path: "/app/workforce", icon: Bot },
  { label: "WorkItems", path: "/app/work-items", icon: ClipboardList },
  { label: "Approvals", path: "/app/approvals", icon: CheckSquare },
  { label: "Leads", path: "/app/leads", icon: UsersRound },
  { label: "Products", path: "/app/products", icon: Tags },
  { label: "Integrations", path: "/app/integrations", icon: PlugZap },
  { label: "Analytics", path: "/app/analytics", icon: BarChart3 },
  { label: "Settings", path: "/app/settings", icon: Settings }
] as const;

export const departmentItems = [
  { label: "Business Supervisor", icon: Layers3 },
  { label: "Sales", icon: UsersRound },
  { label: "Marketing", icon: BarChart3 },
  { label: "Back Office", icon: Settings }
] as const;
