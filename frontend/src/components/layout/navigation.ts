import {
  BarChart3,
  Bot,
  CheckSquare,
  Home,
  Inbox,
  Layers3,
  PlugZap,
  Settings,
  Tags,
  UsersRound
} from "lucide-react";

export const navigationItems = [
  { label: "Overview", path: "/app", icon: Home },
  { label: "Inbox", path: "/app/inbox", icon: Inbox },
  { label: "Approvals", path: "/app/approvals", icon: CheckSquare },
  { label: "Leads", path: "/app/leads", icon: UsersRound },
  { label: "Products", path: "/app/products", icon: Tags },
  { label: "Integrations", path: "/app/integrations", icon: PlugZap },
  { label: "AI Sales Team", path: "/app/ai-team", icon: Bot },
  { label: "Analytics", path: "/app/analytics", icon: BarChart3 },
  { label: "Settings", path: "/app/settings", icon: Settings }
] as const;

export const departmentItems = [
  { label: "Business Supervisor", icon: Layers3 },
  { label: "Sales", icon: UsersRound },
  { label: "Marketing", icon: BarChart3 },
  { label: "Back Office", icon: Settings }
] as const;
