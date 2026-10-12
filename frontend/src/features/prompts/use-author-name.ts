import { useAuthorsQuery } from "./prompt-queries";

/**
 * Who wrote a version: the member's name; "Former member" when the id is no longer a member;
 * "Unknown author" when there is no id or the members list could not be read.
 */
export function useAuthorName(): (userId: string | null) => string {
  const members = useAuthorsQuery();
  return (userId) => {
    if (members.isPending) {
      return "…";
    }
    if (userId === null || members.isError) {
      return "Unknown author";
    }
    const member = members.data.find((candidate) => candidate.user.id === userId);
    return member ? member.user.name || member.user.email : "Former member";
  };
}
