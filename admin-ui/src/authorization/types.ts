export type PermissionEffect = "allow" | "deny";

export interface PermissionAssignment {
    assignment_id: string;
    node: string;
    effect: PermissionEffect;
    priority: number;
    grant: PermissionEffect;
    source_type: string;
    source_id: string;
    starts_at: number | null;
    expires_at: number | null;
    revoked_at: number | null;
}

export interface TeamAuthorizationSnapshot {
    team_id: string;
    permissions: string[];
    content_permissions: string[];
    tester_scopes: string[];
}

export interface AuthorizationSnapshot {
    user_id: string;
    system_permissions: string[];
    effective_permissions: string[];
    grantable_permissions: string[];
    permission_assignments: PermissionAssignment[];
    team: TeamAuthorizationSnapshot | null;
}

function stringArray(value: unknown): value is string[] {
    return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function assignment(value: unknown): value is PermissionAssignment {
    if (!value || typeof value !== "object") return false;
    const item = value as Record<string, unknown>;
    return typeof item.assignment_id === "string"
        && typeof item.node === "string"
        && (item.effect === "allow" || item.effect === "deny")
        && typeof item.priority === "number"
        && (item.grant === "allow" || item.grant === "deny")
        && typeof item.source_type === "string"
        && typeof item.source_id === "string"
        && (typeof item.starts_at === "number" || item.starts_at === null)
        && (typeof item.expires_at === "number" || item.expires_at === null)
        && (typeof item.revoked_at === "number" || item.revoked_at === null);
}

export function parseAuthorizationSnapshot(value: unknown): AuthorizationSnapshot {
    const {t} = useLocale();
    if (!value || typeof value !== "object") {
        throw new Error(t("common.authorizationSnapshotInvalid"));
    }
    const item = value as Record<string, unknown>;
    const team = item.team;
    if (team !== null && team !== undefined) {
        if (!team || typeof team !== "object") {
            throw new Error(t("common.authorizationTeamSnapshotInvalid"));
        }
        const teamRecord = team as Record<string, unknown>;
        if (
            typeof teamRecord.team_id !== "string"
            || !stringArray(teamRecord.permissions)
            || !stringArray(teamRecord.content_permissions)
            || !stringArray(teamRecord.tester_scopes)
        ) {
            throw new Error(t("common.authorizationTeamSnapshotInvalid"));
        }
    }
    if (
        typeof item.user_id !== "string"
        || !stringArray(item.system_permissions)
        || !stringArray(item.effective_permissions)
        || !stringArray(item.grantable_permissions)
        || !Array.isArray(item.permission_assignments)
        || !item.permission_assignments.every(assignment)
    ) {
        throw new Error(t("common.authorizationSnapshotInvalid"));
    }
    return value as AuthorizationSnapshot;
}
import {useLocale} from "../i18n";
