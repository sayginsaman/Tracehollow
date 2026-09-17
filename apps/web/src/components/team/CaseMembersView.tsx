"use client";

import { ROLE_DESCRIPTIONS, ROLE_LABELS, hasSystemPermission } from "@/lib/permissions";
import { useSession } from "@/lib/session-context";

import { useCase } from "../cases/CaseContext";
import { KeyValue, Notice, PageHeader, Panel, RoleBadge } from "../ui";
import { MembersManager } from "./MembersManager";

export function CaseMembersView() {
  const { apiBase, caseDetail, can, role } = useCase();
  const { session } = useSession();
  const canManage = can("case.members.manage");
  return (
    <div className="space-y-6">
      <PageHeader
        title="Members"
        meta={
          <>
            <span>Your role</span>
            <RoleBadge role={role} />
          </>
        }
        description="Only members can open this case. Administrators manage accounts but do not see case content unless they are members."
      />
      {caseDetail.status !== "active" && canManage ? (
        <Notice tone="neutral">This case is archived. Membership can still be changed so the right people can restore or read it.</Notice>
      ) : null}
      <Panel title="People with access">
        <MembersManager
          membersPath={`${apiBase}/members`}
          canManage={canManage}
          accountSearch={hasSystemPermission(session, "accounts.search")}
          currentUserId={session.user.id}
        />
      </Panel>
      <Panel title="What each role can do" description="A member's effective access is the lower of their case role and their account role.">
        <KeyValue items={(["analyst", "viewer"] as const).map((key) => [ROLE_LABELS[key] ?? key, ROLE_DESCRIPTIONS[key]])} />
      </Panel>
    </div>
  );
}
