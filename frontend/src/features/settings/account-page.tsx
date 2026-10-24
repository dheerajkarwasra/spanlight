import { ProfileSection } from "./profile-section";
import { SignOutSection } from "./sign-out-section";

/** Settings › Account: who you are, and signing out. Active sessions live on Security. */
export function AccountPage() {
  return (
    <div className="flex max-w-[720px] flex-col gap-5">
      <ProfileSection />
      <SignOutSection />
    </div>
  );
}
