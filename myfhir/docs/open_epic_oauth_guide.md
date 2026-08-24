# OAuth 2.0 Tutorial

Per Breaking Change Notification Q-7365177, to enhance security in Epic back-end integrations,  **all apps that use
Backend OAuth 2.0** will soon be required to host their public keys at
a [JWK Set URL (JKU)](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-URLS) instead of uploading a static
key to each environment. This requirement does not apply to apps that do not have the "Backend Systems" user type. Refer
to the following table for Epic versions when this requirement will take effect.

|                         |                                                                                                                                                                                                  |                                                                                                                                                                                                  |
|-------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Epic Version**        | **Epic Sandbox JKU Support**                                                                                                                                                                     | **Epic Customer JKU Support**                                                                                                                                                                    |
| February 2025 and prior | _Optional_                                                                                                                                                                                       | _Optional_ - Each customer can choose to enforce JKU support for backend OAuth apps. This enforcement is off by default.                                                                         |
| August 2025             | Vendor Services and Epic on FHIR no longer allow new static key uploads for use in the sandbox.                                                                                                  |                                                                                                                                                                                                  |
| February 2026           | Vendor Services and Epic on FHIR websites no longer allow static key uploads for new apps or new customer download requests. Both sites still support rotating keys for existing live customers. | Vendor Services and Epic on FHIR websites no longer allow static key uploads for new apps or new customer download requests. Both sites still support rotating keys for existing live customers. |
| May 2026                |                                                                                                                                                                                                  | Static keys are no longer supported in customer environments for backend OAuth.                                                                                                                  |

This tutorial complements and aims to replace
the [OAuth 2.0 Specification](https://fhir.epic.com/Documentation/Index?docId=oauth2), which is mostly technical specs.

This tutorial focuses on the [SMART App Launch](http://hl7.org/fhir/smart-app-launch/1.0.0/) framework, which is our
recommended integration method for apps that meet all these criteria:

1. Your application is UI-based, such as a web or native application
2. Your end users have Epic credentials. Clinicians, staff, or administrative users use their Hyperspace credentials,
   while patients and their caretakers use their MyChart (Patient Portal) credentials
3. You meet at least one of the following:

- You'd like to grant your users access to your application using their Epic credentials. In this case you're using
  OAuth 2.0 as a form of Single-Sign-On. This option may allow you to avoid having your
  own [authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) system, where
  users would keep a separate login for your application.
- You'd like your application to launch from and optionally be embedded in Epic, and optionally receive contextual
  information at the time of launch such as the patient's ID.

If your application doesn't meet these criteria, but still wants to pull data from Epic, check out
our [Backend OAuth 2.0 Tutorial](https://fhir.epic.com/Documentation/Index?docId=oauth2&section=BackendOAuth2Guide)
 instead of this one. Otherwise, read on!

Your app may not fit cleanly into one workflow, so also consult
our [Choosing a User Context](https://fhir.epic.com/Documentation?docId=usercontext&section=user-context-choose-context)
 documentation to see if you need multiple components in your application.

Following this tutorial, you'll find information on:

- Choosing the right workflow for your use case
- Understanding how to secure your application using OAuth 2.0
- Building an application using a library
- Implementing a Standalone and/or EHR launch
- Reviewing common considerations for apps integrating with Epic
- Deploying your application at Epic Community Members

# Choosing your Workflow[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=choosing-your-workflow "Copy a link to this section to your clipboard.")

From criteria 3 above, there are 2 relevant workflows that the SMART App Launch is used for:

- Using OAuth 2.0 as a form of Single Sign-On (SSO)
- Launching from and optionally embedding in an Epic workflow

Depending on which workflows you'd like to use, consult the following table for next steps:

| **OAuth 2.0 for SSO** | **Launch from Epic** | **Next Steps**                                                                                                                                                                                          |
|-----------------------|----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Yes                   | No                   | Secure your Application using a Standalone launch, meaning your app initiates an Epic login page.                                                                                                       |
| Yes                   | Yes                  | Secure your Application using an EHR launch, meaning Epic initiates launching your app. This launch includes patient context and can optionally embed your app in Epic (for web apps, not native apps). |
| No                    | Yes                  | Review our launching without SSO appendix                                                                                                                                                               |

# Securing your Application [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=securing-your-application "Copy a link to this section to your clipboard.")

If you plan to use OAuth 2.0 for SSO, review this section with your security team. Using OAuth 2.0 incorrectly can
introduce security vulnerabilities in your system.

If your application includes a database or protected business logic, you should already
have [authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
 and [session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) systems in
place. Your existing, protected app might function like this:

1. A user submits a username and password to you.
2. Your authentication system (on your web server) reviews the request.
3. If the request is valid, your authentication system creates an authenticated user session, such as
   an [HttpOnly Cookie](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#cookies) or a
   token stored in
   your [client-side](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html#html5-web-storage-api).
4. The user takes an action in your app, authenticated by their session cookie or token.

Your goal for integrating the SMART app launch into your app should be to replace your authentication system with the
launch, where Epic authenticates the user. You should use the result of the launch to issue authenticated user sessions
from your existing session system. This is illustrated below:

![](https://fhir.epic.com/Content/images/OAuth2/OAuth-Conf-Diagram.png)

The labeled steps (a-d) are referenced in the session creation overview below.

## Using a Library[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=using-a-library "Copy a link to this section to your clipboard.")

Many OAuth 2.0 libraries are capable of or have documentation for integrating with your own session management system.
Make sure you are using a server-side OAuth 2.0 library, and not a client-side one, so that you can securely issue
sessions using the outcome of the launch. The library requirements below should guide you to a server-side library.

## Session Creation from a Launch [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=session-creation-from-a-launch "Copy a link to this section to your clipboard.")

Below is a high-level overview of how the SSO portion of the SMART app launch works (referencing labeled steps in the
diagram above):

1. (Optional) Epic initiates a launch to your app, including a launch token.
2. You initiate an OAuth 2.0 authorization request.
3. The user authenticates against Epic, and Epic redirects back to you with a one-time-use authorization code (**step
   a**). If your app is launched from Epic, your app will have a launch token to present in this request. In that case,
   the user doesn't re-authenticate.
4. Your web server takes the code and sends it to Epic's token endpoint for review (**step b**).
5. If the token request succeeds (**step c**), your system creates an authenticated user session and returns it to the
   user (**step d**).
    - Epic can provide user identifiers to create or look up the user's account in your system.
    - Epic can also provide contextual information like the patient or visit ID.
6. The user takes an action in your app as usual, protected by their session. If the action requires data from Epic,
   your app can use the access token issued to it to retrieve the required data. We recommend storing this token on your
   server and making API calls from the server.

In step 4/b above, we recommend you redeem the authorization code directly from your web server, and use the result to
immediately issue a user session. This contrasts with client-side libraries such
as [fhirclient](https://www.npmjs.com/package/fhirclient), which redeem the authorization code from the user's browser.
Use a server-side workflow instead, so you can trust the result to establish user sessions.

# Building your Application [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=building-your-application "Copy a link to this section to your clipboard.")

## Choosing a Library[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=choosing-a-library "Copy a link to this section to your clipboard.")

### Using a SMART App Launch Library [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=using-a-smart-app-launch-library "Copy a link to this section to your clipboard.")

A SMART app launch library needs to do all of the following:

- Run on your server, not the end-user's device.
- Support the EHR or standalone launch, depending on the launch type you chose above.
- Support private_key_jwt client authentication. We recommend this over client_secret_basic or client_secret_post.
- Support retrieving extra values from the token response. Use this option if you require a launch context beyond the
  standard patient and encounter identifiers.

### Using an OAuth 2.0 Library [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=using-an-oauth-2-library "Copy a link to this section to your clipboard.")

If you cannot find a SMART app launch library, here are OAuth 2.0 library requirements:

- Run on your server, not the end-user's device.
- Use an [OAuth 2.0 Client Library](https://oauth.net/code/) or
  an [OpenID Connect Relying Party](https://openid.net/developers/certified/) library. Client refers to your role in the
  OAuth 2.0 flow, not whether you have a web server component.
- Support the authorization_code grant type.
- Support refresh_token grant type if you need >1 hour of API access.
- Support adding extra parameters to an authorize request ("aud" and "launch" parameters)
- Support private_key_jwt client authentication. We recommend this over client_secret_basic or client_secret_post.
- Support retrieving extra values from the token response so that you can retrieve any launch context including the
  standard patient and encounter identifiers.

## Sample App Config[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=sample-app-config "Copy a link to this section to your clipboard.")

We've created a sample app to help you with initial development. The app has the following settings:

- User type: Clinicians, Staff, or Administrative Users
- Client ID: e3073934-68c1-4ca7-9b59-3d8b934187f1
- Redirect URIs:
    - http://localhost:3000/callback
    - https://localhost/callback
    - http://localhost:3000/epic-sandbox/callback
    - https://localhost/epic-sandbox/callback
- Incoming APIs:
    - Practitioner.Read (R4)
    - PractitionerRole.Search (R4)
    - Patient.Read (R4)
    - Encounter.Read (R4)
    - Location.Read (R4)
    - Location.Search (R4)
- Private Key:
    - Download a sample private key [here](https://fhir.epic.com/Resources/sampleprivatekey). When registering your own
      app, as a security best practice, you should create your own key pair. The sample key is provided exclusively for
      use with testing in the sandbox and should never be used with a mutual customer for testing or production
      purposes.
    - The attached key is an RSA key (ex: RS384 alg). If your library supports Elliptic Curve algorithms (ex: ES384 alg)
      we recommend using that algorithm instead (requires hosting
      a [JWK Set URL](https://fhir.epic.com/Documentation/Index?docId=oauth2&section=JWKS-URLS)).

## Define your Architecture [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=define-your-architecture "Copy a link to this section to your clipboard.")

After you've identified a library, answer the following questions to define your architecture:

1. How will my system/library differentiate between different Epic community members? See the Supporting Multiple FHIR
   Servers topic below.
2. How will my system/library discover the needed OAuth 2.0 endpoints? See the Endpoint Discovery topic below.
3. What client authentication does my library support?
    1. If it supports private_key_jwt (recommended), follow
       these [steps](https://fhir.epic.com/Documentation/Index?docId=oauth2&section=Creating-Key-Pair) to create private
       keys and public keys. On-premises apps will repeat this per installation.
    2. If it supports client_secret_basic (or _post), secret generation is covered in the Upload Your Credentials
       section below, and your implementation team will create separate secrets for every deployment of your app.
    3. Do not use "none" unless you use a launch without SSO.
4. How will my app be deployed?
    1. If your app is Cloud-Based and if you support private_key_jwt authentication, consider sharing private keys
       across Epic Community Members
    2. If your app is installed On-Premises, or if you use client secret authentication, you must use separate
       authentication credentials per install.

For example, the deployment strategies below use the following architectures:

### Cloud-Based Architecture [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=cloud-based-architecture "Copy a link to this section to your clipboard.")

- Different URL paths (on a single domain) per Epic Community Member
- private_key_jwt authentication
- A single private key shared across Epic Community Members, hosted on a single JWKS URL

### On-Premises Architecture [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=on-premises-architecture "Copy a link to this section to your clipboard.")

- Different origins (in this case, subdomains) per Epic Community Member.
- private_key_jwt authentication
- Unique private keys per Epic Community Members, hosted on separate JWKS URLs (one on each origin)

## Build your Application[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=build-your-application "Copy a link to this section to your clipboard.")

Now that you've identified the URLs and client authentication for your system, build an application with those items so
that Epic can verify requests from your application.

Build an application with the following settings:

- Primary User Type. Choose one, depending on your use case.
    - Patients - For patients and their caretakers authenticating with MyChart
    - Clinicians, Staff, or Administrative Users - For users with Epic Hyperspace accounts
- Check "Incoming API"
- Check "Use OAuth 2.0"
    - In Epic on FHIR, this box is always selected.
- Endpoint URI
    - Enter the URIs for your development environments' OAuth 2.0 redirect_uris. Your library should have documentation
      on this value. Commonly, it ends in /callback.
    - Only add URIs where your <hospital-system> is set to Epic's sandbox environment (example: "epic-sandbox"). When
      activating your app at an Epic community member, you'll be able to provide the community member's <
      hospital-system> value as a license-specific URI. This is covered in the Deploying your Application section.
    - For EHR launched apps, do not include your launch URL unless it is the same value as your redirect_uri. Launch
      URLs are not checked against your registered app.
- Incoming APIs
    - For all apps, we recommend reviewing our default "FHIR resource IDs" below, and requesting the associated "FHIR
      API" for the ones you need (example "encounter" and "Encounter.Read (R4)").
    - For patient-facing apps, we recommend the Patient.Read (R4) and RelatedPerson.Read (R4) APIs for use with Openid
      Connect.
    - For clinician, staff and administrative user-facing apps, we recommend the Practitioner.Read (R4) API for use with
      Openid Connect.
- App FHIR Version. We recommend setting this to R4 for new apps. See
  our [documentation](https://fhir.epic.com/Documentation/Index?docId=appfhirversion) on this topic.
- Check "Is this app a confidential client?"
    - We recommend all apps register as a confidential client, and then upload credentials in the next step.
    - If your application launches without SSO, and specifically does not include a web server component at all, then
      you can keep this unchecked and skip the remaining setup. You are ready to test your app.
- Upload your credentials.
    - For private_key_jwt apps, upload your public keys as
      an [JWKS URL](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-URLS)  (recommended) or
      an [X509 certificate](https://fhir.epic.com/Documentation?docId=oauth2&section=Creating-Key-Pair).
    - For On-Premises apps, provide the keys for your development environment at this time. See the example On-Premises
      deployment below for considerations on private key management in production.
    - For client_secret apps, generate a "Sandbox Client Secret" and copy the secret into your development environment.
      When deploying your app, a similar screen appears where you will create separate client secrets per install. You
      should not share secrets between Epic community member installs.
- "Requires Persistent Access"
    - Check this if your app needs more than one hour of API access. This is uncommon, especially if you only use APIs
      at the beginning of your workflow.
    - If your app does need this access, check that your library supports the "refresh_token" grant type.
    - This is only available if your app is a confidential client.

Now that you've configured your app, save it, and wait 30 minutes for your changes to sync. When testing your app
against the sandbox, use the non-production client ID issued to you.

# Implementing a Standalone Launch [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=testing-a-standalone-launch "Copy a link to this section to your clipboard.")

Use the standalone launch workflow when you'd like to use OAuth 2.0 for SSO, but when you do not need to be launched
from Epic. Your app initiates a login on its own, and Epic presents a login screen where the user can enter their
credentials.

To start, make sure you've reviewed the securing and building your application sections above. Most OAuth 2.0 libraries
can handle the standalone launch out of the box. Here's a diagram of what a standalone launch flow looks
like. ![](https://fhir.epic.com/Content/images/OAuth2/Standalone_Launch.png)

## Test a Standalone Launch [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=example-standalone-launch "Copy a link to this section to your clipboard.")

If you are using the sample application setup above, your application should create an authorize request URL that looks
like this, and should navigate the user's browser to this URL (newlines included for clarity, values are URL encoded):

https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize? client_id=e3073934-68c1-4ca7-9b59-3d8b934187f1&
scope=openid%20fhirUser& response_type=code& redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fepic-sandbox%2Fcallback&
state=example-state-value-should-fail& code_challenge_method=S256&
code_challenge=PHHQCoInTVWPbe78fWbQSftF5f6nn7hgskfLfEm4L6s&
aud=https%3A%2F%2Ffhir.epic.com%2Finterconnect-fhir-oauth%2Fapi%2FFHIR%2FR4

Note a few things about this request:

- aud
    - The aud parameter is required for most workflows, and we recommend all apps include this parameter. Set this
      parameter to the FHIR base URL of the server you intend to call (the "iss" value). Starting in the August 2021
      version of Epic, health care organizations can optionally configure their system to require the aud parameter for
      Standalone and EHR launch workflows if a launch context is included in the scope parameter. Starting in the May
      2023 version of Epic, this parameter is required. The value to use is the base URL of the resource server the
      application intends to access, which is typically the FHIR server.
    - Epic maintains a list of our Community Member's FHIR endpoints
      on [epic.com](https://open.epic.com/MyApps/Endpoints)
    - This parameter is not required for standalone launches unless you request the "launch/patient" or
      "launch/encounter" scope. This means that traditional OAuth 2.0 and OpenID Connect clients do not need to include
      this parameter.
- code_challenge and _method
    - We recommend that all apps use [PKCE](https://tools.ietf.org/html/rfc7636). If you have a native app, see
      the Considerations for Native Apps in the appendix. Most OAuth 2.0 libraries should support PKCE for
      "authorization_code" grants
- state
    - The state parameter is set to a fake value in this example. Your library should auto-generate this for you, and
      the redirect back to your app should fail if the state does not match the generated one. This is used to mitigate
      login CSRF attacks. See [RFC 6819](https://www.rfc-editor.org/rfc/rfc6819#section-4.4.1.8) and the
      OWASP [CSRF Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#login-csrf).
- scope
    - The scope parameter is set to "openid fhirUser" (space delimited list). Epic issues additional scopes based on the
      incoming APIs you configured on your application.
    - Remember to add scope "launch" if you later add EHR Launch support. Do not add it for standalone launches.

A login screen appears where you need to enter your sandbox user's credentials. Ask your organization's administrator to
share your sandbox username and password with you.

After you're able to successfully redeem the code for an access token, do one of the following:

- If your target workflow is the EHR launch, review the next section.
- Otherwise, if your target workflow is the standalone launch, continue to the Identifying the User section below

# Implementing an EHR Launch [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=testing-an-ehr-launch "Copy a link to this section to your clipboard.")

The EHR launch flow allows Epic to launch your application and inform it of what user, patient and encounter was open at
the time of launch. The user authenticates at the beginning of their Epic workflow and can launch your app without
re-authenticating. Here's a diagram of what an EHR launch flow looks
like. ![](https://fhir.epic.com/Content/images/OAuth2/EHR_Launch.png)

To start, make sure you've reviewed the securing and building your application sections above. If you chose an OAuth 2.0
library that does not explicitly support the EHR launch, you need to customize it as described below. If you chose an
EHR Launch library, skip to the Specific Redirect URIs section.

## Customize an OAuth 2.0 Library [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=customize-an-oauth-2-library "Copy a link to this section to your clipboard.")

If you cannot find an EHR launch library for your framework, we recommend customizing an OAuth 2.0 library as a starting
point. In this case, you should implement the standalone launch first, and then customize it for an EHR launch. Then, we
recommend turning off your standalone launch if you do not plan to use it in production. Do the following
customizations:

### Launch Endpoint[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=launch-endpoint "Copy a link to this section to your clipboard.")

Your OAuth 2.0 library should create a redirect_uri for you, but you'll need to create a launch URL on your own. This
endpoint is used for [step 1](http://hl7.org/fhir/smart-app-launch/1.0.0/#ehr-launch-sequence) in the EHR launch
sequence, where the user's browser navigates to an endpoint like this:

https://app.com/launch? **iss**=https%3A%2F%2Fehr%2Ffhir& **launch**=xyz123

This request has three main pieces:

- The URL of https://app.com/launch

- This is defined by you, and you provide this to an Epic community member to configure in their system.
- Note this URL does not include a "<hospital-system>" value. We recommend you have a shared launch URL across hospital
  systems, and use the "iss" parameter defined below to differentiate systems.

- The iss parameter

- This is also known as the FHIR base URL and uniquely identifies the Epic community member that is initiating the
  request.
- Your application should create an allowlist of iss values (AKA resource servers) to protect
  against [phishing attacks](https://datatracker.ietf.org/doc/html/rfc6819#section-4.6.4). Rather than accepting any iss
  value in your application, only accept those you know belong to the organizations you integrate with.
- Epic maintains a list of our Community Member's FHIR endpoints
  on [open.epic.com](https://open.epic.com/MyApps/Endpoints)

- The launch parameter

- You should treat this as an opaque token, and just include it in your later request (covered below).

### Additional Authorize Request Parameters [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=additional-authorize-request-parameters "Copy a link to this section to your clipboard.")

After you've validated the iss and launch parameters, your server will initiate an authorize request. You'll need to add
the following parameters as defined in the SMART app launch
"[App](http://hl7.org/fhir/smart-app-launch/1.0.0/#step-1-app-asks-for-authorization)
 [asks for Authorization](http://hl7.org/fhir/smart-app-launch/1.0.0/#step-1-app-asks-for-authorization)" step:

- aud - The iss value from the launch request.
- scope - We recommend using "launch openid fhirUser" (space-delimited list), but "launch" at least is required. Epic
  issues additional scopes based on the "Incoming APIs" you configured on your application.
- launch - The launch parameter provided by the EHR at the launch endpoint above.

## Specific Redirect URIs[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=specific-redirect-uris "Copy a link to this section to your clipboard.")

We recommend your launch URL be the same for all hospital systems, and that you have multiple redirect URIs each
specific to one system. Your launch should use the "iss" to differentiate between health systems and redirect to their
specific URI.

For example, your app may have the following endpoints:

- https://app.com/launch
- https://app.com/health-system-a/callback
- https://app.com/health-system-b/callback

A launch from health-system-a would look like this:

1. The health system will launch to:
    1. https://app.com/launch?iss=https%3A%2F%2Fsystem-a.fhir.com%2Fv1&launch=abc123
2. And when your server constructs an authorization request, it would use this redirect_uri:
    1. https://app.com/health-system-a/callback

In this case, your server mapped on the "iss" value provided to choose the right <health-sytem> value.

## Test an EHR Launch[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=example-ehr-launch "Copy a link to this section to your clipboard.")

Once you've built an application, you can test your integration with
the [Hyperdrive Client Testing Harness](https://open.epic.com/Hyperdrive/Hyperdrive), which supports SMART on FHIR
testing, and use that application's client ID in your test requests.

In either of these tools, you provide your launch URL and the tool will dynamically append the "iss" and "launch"
parameters when you run a test.

A launch of the sample app above would look like this (newlines included for clarity, values are URL encoded):

http://localhost:3000/launch? launch=[omitted]&
iss=https%3A%2F%2Ffhir.epic.com%2Finterconnect-fhir-oauth%2Fapi%2FFHIR%2FR4

In response, your app would navigate the user's browser to a new URL (newlines included for clarity, values are URL
encoded). Note the redirect_uri includes "epic-sandbox", which is the <health-system> value this app chose for the
sandbox's "iss". In addition, the request should include the "launch" scope, and also include the launch token presented
above:

https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize? client_id=e3073934-68c1-4ca7-9b59-3d8b934187f1& scope=
**launch**%20openid%20fhirUser& response_type=code&
redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fepic-sandbox%2Fcallback& state=example-state-value-should-fail&
code_challenge_method=S256& code_challenge=PHHQCoInTVWPbe78fWbQSftF5f6nn7hgskfLfEm4L6s&
aud=https%3A%2F%2Ffhir.epic.com%2Finterconnect-fhir-oauth%2Fapi%2FFHIR%2FR4 **launch**=[omitted]

Note that Epic also supports POST-Based authorization for EHR Launches (but not standalone launches), allowing you to
submit a traditional HTML form (second
example [here](https://www.hl7.org/fhir/smart-app-launch/app-launch.html#for-example-1)) for the authorize request. This
moves the request parameters from the query string into the body of the form. If you use this method while embedded in
Epic, you will need to allow the page to be embedded in an iframe.

### Handling the State Parameter [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=handling-the-state-parameter "Copy a link to this section to your clipboard.")

We recommend following
the [SMART App Launch Framework's](http://hl7.org/fhir/smart-app-launch/1.0.0/#step-1-app-asks-for-authorization)
 recommendation that the "state" parameter be an unpredictable value … with at least 122 bits of entropy (e.g., a
properly configured random UUID is suitable), and not store any actual application state in the state parameter.

If you must store data in the "state" parameter, we recommend you use POST-Based authorization (discussed above). Large
state values combined with Epic launch tokens may cause the request URL to exceed the authorization server's maximum URL
length. By moving the data to a POST body, your request parameters do not contribute to the URL length.

## Embedding Tips[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=embedding-tips "Copy a link to this section to your clipboard.")

If you are writing a web-based application that you intend to embed in Epic, you may find that your application may
require minor modifications. We offer a
simple [testing harness](https://open.epic.com/Tech/TechSpec?spec=TestingHarness.zip&specType=tools) to help you
validate that your web application is compatible with the embeddable web application viewer in Epic. As embedding your
application in Hyperspace may not behave the same as presenting it in a browser, ensure that your app works with the
testing harness and that you can run multiple instances of the app at the same time within the same session (click the
"Use Two Browsers" checkbox). Our [launching & embedding document](https://fhir.epic.com/Documentation?docId=launching)
 has more details, and we provide implementation options below.

### Embedding in Multiple Tabs [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=embedding-in-multiple-tabs "Copy a link to this section to your clipboard.")

Epic can embed multiple instances of your application by opening them in separate browser tabs. Given Epic can have
multiple patients open at once, your app needs to handle having multiple browser tabs open at once, and remember what
patient is open in each tab.

Rather than using HTML5 SessionStorage for tab segregation, you need to use a dedicated URL parameter representing the
tab. You can then reference the launch session in that tab from the URL. There are several URL parameter options, such
as:

- A path parameter (/app/{tabId})
- A query string parameter (/app?tabId={tabId})
- A URL fragment parameter (/app#tabId={tabId})

Most OAuth 2.0 libraries do not support tab-specific workflows out of the box, and you may need to customize the library
to achieve this. This involves at least:

- Having the library redirect to a tab-specific URL after the launch.
- Securing the tab-specific data (patient context) behind your session management system.

Epic's Hyperspace browser does not support segregated session storage. If your app stores tab-specific data in session
storage, you need to use the unique tabId from above to store off this data per tab. You can store/retrieve any data
like this:

window.sessionStorage.setItem (tabId,"tab-specific-data")

window.sessionStorage.getItem (tabId) // Returns "tab-specific-data"

### Other Browser Options [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=system-default-browser-option "Copy a link to this section to your clipboard.")

Epic community members can configure your app to launch in a floating window or the system default browser in case your
app cannot support embedding. Note the system default browser is not recommended because switching between patients in
Epic does not close your app. The user might not realize the open patient is different between Epic and your
application, which might lead to them entering information or making care decisions based on a different patient.

Any apps that cannot support embedding in an iframe should attempt a floating window launch, and only use the system
default browser as a fallback.

# App Architecture Considerations [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=app-architecture-considerations "Copy a link to this section to your clipboard.")

This section includes some common considerations for app developers, including:

- Supporting Multiple FHIR Servers - Since each Epic community member has their own copy of Epic.
- Endpoint Discovery - So you can limit your configuration to one or two URLs.
- Retrieving Launch Context - Outlines different ways Epic can share contextual data with your app.
- Identifying the User - Outlines Epic's support for sharing user identifiers and other user info.

## Supporting Multiple FHIR Servers [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=supporting-multiple-fhir-servers "Copy a link to this section to your clipboard.")

Each Epic community member has a separate copy of Epic, and their own unique database of users and patients. This means
that each Epic community member has their own authorization and servers, and that your app needs to differentiate
between them. For example, patient records between different systems should not be mixed, because identifiers such as
MRNs are not globally unique.

### Defining your Endpoints [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=defining-your-endpoints "Copy a link to this section to your clipboard.")

The two most common strategies we see for handling tenancy in apps are:

- **Unique URL paths**: https://app.com/<hospital-system>/app
- **Unique subdomains**: https://<hospital-system>.app.com/app

- Note that apps installed on-premises (hosted by Epic community members) likely have entirely different domains per
  community member.

Epic community members are most familiar with these strategies, and we recommend against using others such as query
string parameters.

### Multitenancy and ID Segregation [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=multitenancy-and-id-segregation "Copy a link to this section to your clipboard.")

<hospital-system> is defined by you, and you can match this to your own [tenant](https://en.wikipedia.org/wiki/Multitenancy) structure. You should always segregate data from different Epic community members, and keep in mind the following identifiers are not globally unique:

- FHIR IDs
- MRNs and other Epic internal identifiers

These IDs are only unique when stored off with an identifier-system, such as:

- FHIR ID + resource type + the FHIR Base URL
- ID + ID Concept + <hospital-system> - From our ID Types
  tutorial, [OIDs](https://fhir.epic.com/Documentation/Index?docId=epicidtypes) are generally a combination of ID
  Concept and <hospital-system>

## Endpoint Discovery[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=endpoint-discovery "Copy a link to this section to your clipboard.")

Epic maintains a list of our Community Member's FHIR endpoints
on [open.epic.com](https://open.epic.com/MyApps/Endpoints), where each <hospital-system> as defined above has one
endpoint. The sandbox's R4 FHIR endpoint for example is:

https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4

Starting from this endpoint, you can manually edit or programmatically discover the other endpoints used by your
library. Here are some examples:

- For the OAuth 2.0 authorize and token endpoints, you can use the programmatic methods
  mentioned [here](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Conformance-Statement).
  If your app does programmatic discovery, cache these values on startup or pre-configure them, rather than making the
  request for every login.
- For
  the [Openid Connect issuer](https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfigurationRequest)
   endpoint, replace /api/FHIR/R4 with /oauth2. For example:

https://fhir.epic.com/interconnect-fhir-oauth/oauth2

## Retrieving Launch Context [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=retrieving-launch-context "Copy a link to this section to your clipboard.")

The SMART App Launch delivers patient context by adding parameters to the OAuth
2.0 [token endpoint response](http://hl7.org/fhir/smart-app-launch/1.0.0/scopes-and-launch-context/index.html#launch-context-arrives-with-your-access_token).
Your library needs to support retrieving these extra values from the token response, such as patient and encounter
below:

{
"access_token": "[omitted]",
"token_type": "Bearer",
"expires_in": 3599,
"id_token":
"eyJhbGciOiJSUzI1NiIsImtpZCI6ImxpQ3VsVElhaXRVempmVWgyQXFOaU1ybzQ3WDlIY1ZjZDlYUGk4TERKS0E9IiwidHlwIjoiSldUIn0.eyJhdWQiOiJkODZhNjBiNi01MDExLTRjOTEtYTg2Yi1iYmM0YWNjZjkyZTIiLCJleHAiOjE2ODA3MjUyOTEsImZoaXJVc2VyIjoiaHR0cHM6Ly92ZW5kb3JzZXJ2aWNlcy5lcGljLmNvbS9pbnRlcmNvbm5lY3QtYW1jdXJwcmQtb2F1dGgvYXBpL0ZISVIvUjQvUHJhY3RpdGlvbmVyL2V0QnpNYVE0ZEhONU9wVC1DTUZEMzhRMyIsImlhdCI6MTY4MDcyNDk5MSwiaXNzIjoiaHR0cHM6Ly92ZW5kb3JzZXJ2aWNlcy5lcGljLmNvbS9pbnRlcmNvbm5lY3QtYW1jdXJwcmQtb2F1dGgvb2F1dGgyIiwibm9uY2UiOiI4ejloXzAwbzNVeGo4ZTA2dVhXdVhQSjBWZEo3OGdXTW1nTFREa2Q4aFlVIiwic3ViIjoiZXRCek1hUTRkSE41T3BULUNNRkQzOFEzIn0.N8Ru9dlHN4ephBvYcv6qslcF3lMpAQEMspQAm7vLk-S9NekO5L1Sup4H1e7sDGsiUtlyvV012lxj45RAN9iW8NbqbvYM3yjFeonm19rL6T5AOS09Yf694Mg-gFOLamGeCnhITm7UNaEWOvso1GoXCsoSujfR3jEPjDG5mEDf9ArWNLdoy6_mHG2xCGELhuk3B4NIDvsU_FKitpx-_
lNm1-FZNoot2taRW3sMEj9bziIz8wKhtNYPY1lDwoAfbIww-t1umMQsZ3KPnCIqTKOcmiWj2nozJlOW7ouuyeJouSK7Qk-90N5UK5zgQTk_8unAYWYOd-5WClGzjCkzKC2DdQ",
"refresh_token": "[omitted]",
"scope": "user/AdverseEvent.Read user/AdverseEvent.read user/AllergyIntolerance.read user/AllergyIntolerance.write
user/Appointment.read user/Binary.Read user/Binary.read user/BodyStructure.read user/CarePlan.read user/CareTeam.read
user/Communication.read user/Communication.write user/Condition.read user/Condition.write user/Consent.read
user/Coverage.read user/CriteriaReview.read user/CriteriaReview.write user/Device.read user/DiagnosticReport.read
user/DiagnosticReport.write user/DocumentReference.Read user/DocumentReference.read user/DocumentReference.write
user/Encounter.read user/Endpoint.read user/EpisodeOfCare.read user/ExplanationOfBenefit.Read
user/ExplanationOfBenefit.read user/Flag.read user/Goal.read user/ImagingStudy.Read user/ImagingStudy.read
user/Immunization.read user/List.read user/Location.read user/Medication.read user/MedicationRequest.read
user/Observation.read user/Observation.write user/Organization.read user/Patient.read user/Patient.write
user/Practitioner.read user/PractitionerRole.read user/Procedure.read user/ProcedureRequest.Read user/Questionnaire.read
user/QuestionnaireResponse.Read user/QuestionnaireResponse.write user/RelatedPerson.Read user/RequestGroup.read
user/ResearchStudy.Read user/ResearchStudy.read user/ReviewCollection.read user/ReviewCollection.write
user/ServiceRequest.read user/Specimen.Read user/Specimen.read user/Substance.Read user/Substance.read user/Task.read
user/Task.write user/ValueSet.Read fhirUser launch launch/encounter launch/patient offline_access openid",
"__epic.dstu2.patient": "TLqtVI-V.rX88jQHUnHxO8Npvd9Gcuz5fFeZuNPSeURIB",
"encounter": "eUAW2-QkdwCE7KH7awS8sfw3",
"location": "eweKBcq8ruM-1MzZAX-A-0uj2rDm4SMniPONiaM5VGQk3",
"loginDepartment": "e4W4rmGe9QzuGm2Dy4NBqVc0KDe6yGld6HW95UuN-Qd03",
"need_patient_banner": "false",
"patient": "eRp.HuKDlKB8MhAkzHFthQQ3",
"smart_style_url":
"https://fhir.epic.com/interconnect-fhir-oauth/api/epic/2016/EDI/HTTP/style/33500004931/I0YwRjJGNHwjQzEyMTI3fCMxQUFCRkZ8I0QzRDhERXwjODZCNTQwfCMwMDAwMDB8MHB4fDEwcHh8fEFyaWFsLCBzYW5zLXNlcmlmfCdTZWdvZSBVSScsIEFyaWFsLCBzYW5zLXNlcmlmfHw%3D.json
"
}

Epic supports a large [library](https://fhir.epic.com/Documentation?docId=launching&section=token_library) of context
tokens, including these FHIR resource IDs (included by default):

| **Key**         | **FHIR API**     | **Explanation**                                         | **Available For**                                                                                  |
|-----------------|------------------|---------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| patient         | Patient.Read     | Patient chart open during the launch                    | Most contexts, except for dashboard-type launches                                                  |
| encounter       | Encounter.Read   | Visit open during the launch                            | Provider-facing contexts, where a visit is open during the launch                                  |
| location        | Location.Read    | Current location of the patient from the launched visit | Same as encounter, see above                                                                       |
| loginDepartment | Location.Read    | Department the provider logged into                     | Only in launches from Hyperspace and provider-facing mobile apps                                   |
| appointment     | Appointment.Read | Scheduled appointment that became the current visit     | Available if a visit is open during the launch, and if that visit began as a scheduled appointment |

Note that the tokens documented above are not always returned, and this is controlled by the specific workflow your app
is used in. Limit the launch context your app requires to the minimum data needed and explain the context and expected
workflow to your installing community members. For example, does your app need visit context, or just patient context?

## Identifying the User[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=identifying-the-user "Copy a link to this section to your clipboard.")

If you are using an Openid Connect Relying Party library, it handles user authentication by relying on
the ["iss" URL and "sub" claim](https://openid.net/specs/openid-connect-core-1_0.html#ClaimStability).

Note the "iss" for an OIDC flow is not the same as the "iss" from the first step of an EHR launch. For example:

- Fhir "iss": https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4
- Openid Connect "iss": https://fhir.epic.com/interconnect-fhir-oauth/oauth2

One simple user account strategy is to record the user's "sub" as the primary key in a database dedicated to one Openid
Connect Issuer (See Multitenancy and ID Segregation above). Using this system, you can create an account on-the-fly for
anyone that can launch your app from Epic, optionally retrieving their demographics using the strategies below.

### On-the-Fly Account Creation [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=on-the-fly-account-creation "Copy a link to this section to your clipboard.")

If your app requests the "openid" and "fhirUser" scopes, your id_token includes a claim where you can retrieve
additional demographics about the user by making an API call.

For example, a launch from the sandbox could return these claims (among other claims omitted):

{

"fhirUser": "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/Practitioner/eUXiD4IyBLJFoR2VHOjfTdg3",

"sub": "eUXiD4IyBLJFoR2VHOjfTdg3"

}

So, an app scoped for the Practitioner.Read (R4) API could make a GET request to this URL (using your access token).
This resource returns name, email, gender, and NPI, among other identifiers such as employee record ID. Note that not
all users have an NPI.

Below is an example "identifier" object showing a provider's NPI. You can use the "system" value of "urn:oid:
2.16.840.1.113883.4.6" across all Epic community members because it is
the [global OID](https://oidref.com/2.16.840.1.113883.4.6) for NPI:

{

"use": "usual",

"type": {

    "text": "NPI"

},

"system": "urn:oid:2.16.840.1.113883.4.6",

"value": "1627363736"

}

Here is an example object showing a user's employee record ID. Note the system value is
an [OID](https://fhir.epic.com/Documentation/Index?docId=epicidtypes) that will vary per Epic community member, and
between non-production and production:

{
"use": "usual",
"type": {
"text": "EXTERNAL"
},
"system": "urn:oid:1.2.840.114350.1.13.0.1.7.2.697780",
"value": "FAMMD"
}

See
our [documentation](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Using-Access-Token)
 for more information on making FHIR requests using your access token.

# Deploying your Application [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=deploying-your-application "Copy a link to this section to your clipboard.")

Your app should have been issued two client IDs: a non-production one and a production one. These client IDs are used
for all your Epic community member installs, not just Epic's sandbox. Make sure that your app's production servers are
using your production client ID.

Your application should use separate key pairs for non-production and production environments.

After your app is deployed and made available to Epic community members, you should see requests for your application
within the website. Depending on your app's architecture, your implementation team will provide specific settings per
every install, using the instructions below. Note, only non-production URLs (-np, as a convention) are shown, and you
will repeat this setup with your production URLs (-p) and production client ID as well.

## Example App Deployments [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=example-app-deployments "Copy a link to this section to your clipboard.")

Below are 2 example app deployments, one for a Cloud-Based App and one for an On-Premises App. Review these to see what
fields you should provide on the website when licensing your app for a given Community Member.

### Cloud-Based App[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=cloud-based-app "Copy a link to this section to your clipboard.")

This example assumes your application supports private_key_jwt authentication. If not, and if you are using client
secrets, you must use different secrets for each Epic Community Member.

An example cloud-based application with two installing health systems might have the following list of endpoints:

- https://my-app-np.com/config/jwks.json
- https://my-app-np.com/app/launch
- https://my-app-np.com/app/health-system-a/callback
- https://my-app-np.com/app/health-system-b/callback

Note there is only one jwks.json (and a production one not listed) because the app can in principle protect a shared
private key in the cloud. If you do not want to re-use private keys across Epic community members, review
the On-Premises App model below.

Steps:

1. Make sure your app has an app-level JWK Set URL listed, because this is not a download specific value for cloud-based
   apps. Your organization's administrator should be able to edit this even after your application has been activated.
   Click "edit" at the bottom of your build apps page to unlock this
   field:![](https://fhir.epic.com/Content/images/OAuth2/Cloud-app-JWKS.png)
2. When provisioning, un-check and remove the app-level endpoint URIs, and just supply the health system specific
   values: ![](https://fhir.epic.com/Content/images/OAuth2/Cloud-License-URIs.png)
3. When provisioning, select the "JWK Set URL" option and explicitly use the app-level value. If the "JWK Set URL"
   option is not available, see step 4: ![](https://fhir.epic.com/Content/images/OAuth2/Cloud-License-JWKS.png)
4. (Conditional) Provide your public key manually
   in [X509 format](https://fhir.epic.com/Documentation?docId=oauth2&section=Creating-Key-Pair) if the JWK Set URL
   option is disabled based on the community member's Epic version:
    ![](https://fhir.epic.com/Content/images/OAuth2/Shared-X509.png)

### On-Premises App[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=on-premises-app "Copy a link to this section to your clipboard.")

An example on-premises (on prem) application with two installing health systems may have the following list of
endpoints. Note there are multiple public key endpoints because on-premises apps should always have a JWK Set URL
pointing at the community member's specific instance of your application, and its unique public key:

- https://health-system-a.my-app-np.com/config/jwks.json
- https://health-system-a.my-app-np.com/app/launch
- https://health-system-a.my-app-np.com/app/callback
- https://health-system-b.my-app-np.com/config/jwks.json
- https://health-system-b.my-app-np.com/app/launch
- https://health-system-b.my-app-np.com/app/callback

Steps:

1. When provisioning, un-check and remove the app-level endpoint URIs, and just supply the health system specific
   values: ![](https://fhir.epic.com/Content/images/OAuth2/OnPrem-License-URIs.png)
2. When provisioning, select the "JWK Set URL" option, un-check "Use app-level JWK Set URL", and provide this health
   system's specific URL. If the "JWK Set URL" option is not available, see step 3:
    ![](https://fhir.epic.com/Content/images/OAuth2/OnPrem-License-JWKS.png)
3. (Conditional) Provide your public key manually
   in [X509 format](https://fhir.epic.com/Documentation?docId=oauth2&section=Creating-Key-Pair) if the JWK Set URL
   option is disabled based on the community member's Epic version:
    ![](https://fhir.epic.com/Content/images/OAuth2/Shared-X509.png)

# Appendix[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=appendix "Copy a link to this section to your clipboard.")

## Considerations for Native Apps [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=considerations-for-native-apps "Copy a link to this section to your clipboard.")

There are additional security considerations when launching a native app to sufficiently protect the authorization code
from malicious apps running on the same device. There are a few options for how to implement this additional layer of
security:

- Platform-specific link association: Android, iOS, Windows, and other platforms have introduced a method by which a
  native app can claim an HTTPS redirect URL, demonstrate ownership of that URL, and instruct the OS to invoke the app
  when that specific HTTPS URL is navigated to. Apple's iOS calls this feature
  "[Universal Links](https://developer.apple.com/library/archive/documentation/General/Conceptual/AppSearch/UniversalLinks.html)
  "; Android, "[App Links](https://developer.android.com/training/app-links/)"; Windows,
  "[App URI Handlers](https://docs.microsoft.com/en-us/windows/uwp/launch-resume/web-to-app-linking)". This association
  of an HTTPS URL to native app is platform-specific and only available on some platforms.
- Proof Key for Code Exchange (PKCE): This is a standardized, cross-platform technique for public clients to mitigate
  the threat of authorization code interception. However, it requires additional support from the authorization server
  and app. PKCE is described in [IETF RFC 7636](https://tools.ietf.org/html/rfc7636) and supported starting in the
  August 2019 version of Epic. Note that the Epic implementation of this standard uses the S256 code_challenge_method.
  The "plain" method is not supported.

Starting in the August 2019 version of Epic, the Epic implementation of OAuth 2.0 supports PKCE, and Epic recommends
using PKCE for native mobile app integrations. For earlier versions or in cases where PKCE cannot be used, Epic
recommends the use of Universal Links/App Links in lieu of custom redirect URL protocol schemes.

## private_key_jwt authentication [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=private_key_jwt-authentication "Copy a link to this section to your clipboard.")

During an OAuth 2.0 flow, your server may be required to authenticate itself to the token endpoint. We recommend
private_key_jwt authentication over client_secret_basic (and client_secret_post) for a few reasons:

- It uses asymmetric cryptography, so you never have to distribute your credential (private key).
- It simplifies deploying your application because private keys can be reused across Epic community members (for
  cloud-based apps).
- When your public key is hosted on a JWK Set URL, you can easily rotate your public (and private) keys to help you
  meet [NIST's recommendations](https://csrc.nist.gov/publications/detail/sp/800-57-part-1/rev-5/final) on private key
  rotation.

### Comparison to Mutual TLS [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=comparison-to-mutual-tls "Copy a link to this section to your clipboard.")

private_key_jwt authentication is similar to mutual TLS in that both use asymmetric cryptography. Both techniques give
you strong assurance of who the app is and do not require you to manually distribute a secret like a password.

The two techniques differ in a few key ways:

- Mutual TLS can use a certificate chain to validate the expected public key, whereas private_key_jwt relies on the app
  developer pre-registering a public key with Epic.
- With private_key_jwt authentication, the app authenticates only once per hour per launch. With mutual TLS, the app
  authenticates on every web service call.
- Mutual TLS is conducted at the transport layer, whereas private_key_jwt is conducted in the application layer (see
  the [OSI Model](https://en.wikipedia.org/wiki/OSI_model)).

Practically, mutual TLS is more difficult to maintain because Epic community members need to register public keys
manually through their Client Systems team. With private_key_jwt, Epic handles authentication directly in the Epic
database.

## Launching Without SSO[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=launching-without-sso "Copy a link to this section to your clipboard.")

### Use Cases[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=use-cases "Copy a link to this section to your clipboard.")

You might want to launch your application from Epic but not use the launch to authenticate your users. In general, we
see one of two use cases for this:

**Re-authenticating on Launch**

While not our recommended workflow, your app can re-authenticate users during the launch and just use the launch to
provide access to patient data.

**No Authentication**

Your application does not include a component that needs protecting. Examples of un-protected applications include:

- Public calculators, where anyone can submit data to your server without authentication and see the results.
- Front-end data visualization apps, where data loaded from the EHR is processed on the user's device.

If your un-protected application does not require user or patient context, then you do not need to use the SMART App
Launch at all. You can simply provide a static URL for your website or Native app that Epic Community Members can
configure in their system. This does not require registration with Epic.

If your un-protected application requires user or patient context, then you should use the launch to avoid putting PHI
and PII into the page's URL.

### Choosing a Library without SSO [](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=choosing-a-library-no-sso "Copy a link to this section to your clipboard.")

Our recommendation for these apps is to use the same infrastructure as an application using OAuth 2.0 for SSO. However,
for applications with no web server component this added infrastructure may be more complex than you need.

As an alternative, you can use a client-side OAuth 2.0 library such as the common,
opensource [fhirclient](https://www.npmjs.com/package/fhirclient) library (for single-page-apps) or one from
the [AppAuth SDK](https://appauth.io/)  (for native apps). Use caution when using these libraries because they cannot be
used to protect your app's web server (if you have one). They only secure the connection between your client-side and
the EHR's authorization server.

If you decide to use a client-side OAuth 2.0 library, you will not register credentials (public keys or client secrets)
when building your app and will configure your library to use client authentication type "none." This is called out in
the "Upload your credentials" step of the Building your Application section.

### Persistent Access[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=persistent-access "Copy a link to this section to your clipboard.")

If you are using a client-side OAuth 2.0 library, you cannot use the "refresh_token" grant type for persistent access to
APIs (>1 hour of access). If you need persistent access, we recommend using a server-side OAuth 2.0 library with refresh
tokens, where the refresh token is stored on your server and protected by your session management system.

If you cannot add a server component to your application, you can add dynamic client registration to your client-side
only application and follow the
directions [here](https://fhir.epic.com/Documentation/Index?docId=oauth2&section=Standalone-Oauth2-OfflineAccess-0) to
use your initial access token to get persistent access. This app is only available for Patient-facing applications at
this time.

### SMART Scopes[](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=smart-scopes "Copy a link to this section to your clipboard.")

[SMART scopes](https://hl7.org/fhir/smart-app-launch/1.0.0/scopes-and-launch-context/index.html) are returned in the
scope parameter of the OAuth 2.0 token endpoint response and determine which resources an application has permission to
access and what actions the application is permitted to take. The scopes provided are based on the scopes that your app
requested in
your [authorize request](https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=additional-authorize-request-parameters)
 as well as the incoming APIs you have selected on your app page.

As of the November 2024 version of Epic, both SMART v1 and SMART v2 scope formatting are supported. In the August 2024
and prior versions, only SMART v1 scopes are supported.

The actions defined by SMART v1 scopes are .read and .write. SMART v2 scopes use CRUDS syntax and actions include: .c –
create, .r – read, .u – update, .d – delete, and .s – search.

For example, the Observation.Create resource corresponds to either:

- **SMART v1:** {user-type}/Observation.write
- **SMART v2:** {user-type}/Observation.c

#### Which version should you select?

If your app uses SMART v1 scopes or if your app does not use SMART scopes at all, you should select SMART v1.

If your app uses SMART v2 scopes, you should select SMART v2.

_Note: Prior to the November 2024 version of Epic, SMART v1 scope formatting will be returned regardless of the SMART
Scope Version selected on your app page. If your app uses SMART scopes and your customer is on a version of Epic earlier
than November 2024, you must support SMART v1 scopes._

# Epic as Backend Client Using OAuth 2.0 Client Credentials Grant

_Updated: February 23, 2026_

## High Level Technical Summary

Epic's web API client supports the client_credentials OAuth 2.0 grant type,
per [RFC 6749](https://datatracker.ietf.org/doc/html/rfc6749). A JSON Web Key Set Url (JKU), specific to a given health
system, is pre-registered with the external authorization server in exchange for a client id. When calling a web API,
the Epic client signs a JSON Web Token (JWT) with a private key to obtain an access token with a client_assertion_type
of urn:ietf:params:oauth:client-assertion-type:jwt-bearer per [RFC 7515](https://datatracker.ietf.org/doc/html/rfc7515)
 and [RFC 7523](https://datatracker.ietf.org/doc/html/rfc7523#section-2.2).

The authorization server retrieves a list of public keys from the pre-registered JSON Web Key Set Url (JKU). The
returned key set contains one to many public keys, each with a key identifier (kid). The JWT used to authenticate to the
authorization server identifies the kid of the public key corresponding to the private key used to sign the JWT.

### Key Rotation

JWK Set URLs streamline implementation and make key rotation feasible. Epic's web API client's keys can be automatically
or manually rotated. Once a new key-pair is generated, the previous key-pair is no longer used to sign credentials. The
previous key-pair remains accessible from the JKU for a short window of time.

### Registration

Each health system using Epic software should manually register for their own client id with the authorization server
and will provide their own JKU.

Additionally, the resource server hosting the APIs accessed by Epic's web API client registers a client id with Epic,
which is used to audit outgoing API calls.

### Signing Algorithms

Epic's web API client supports the following signing algorithms:

- RSASSA-PKCS1-v1_5 using SHA-256 [RS256]
- RSASSA-PKCS1-v1_5 using SHA-384 [RS384]
- RSASSA-PKCS1-v1_5 using SHA-512 [RS512]
- ECDSA using P-256 and SHA-256 [ES256]
- ECDSA using P-384 and SHA-384 [ES384]
- ECDSA using P-512 and SHA-512 [ES512]

Starting in August 2025, if you use an RSA algorithm, you have the following options for the RSA key's bit length:

- 2048 bit
- 3072 bit
- 4096 bit
- 8192 bit

### Examples

#### Example Token Request

```
POST https://as.example.com/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=<URL encoded client id from authorization server>&client_assertion_type=urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer&client_assertion=eyJhbGciOiJFUzM4NCIsImprdSI6Imh0dHBzOi8vdmVuZG9yc2VydmljZXMuZXBpYy5jb20vaW50ZXJjb25uZWN0LWFtY3VycHJkLW9hdXRoL29hdXRoMi9rZXlzLzIvRTNEOUE5MkNGNTE2QkNEMTVGMjNCMjU0RkNCNTVBNjMiLCJraWQiOiJwLzVwWUo0SjNQVU9MeGhRb0NqZk5KRkNtOE01aTJicVN0bjcvODRGT1B3PSIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJodHRwczovL2VuOWYzMWU1d2kzeG1mcS5tLnBpcGVkcmVhbS5uZXQiLCJleHAiOjE2OTIwMjY2NTAsImlhdCI6MTY5MjAyNjUzMCwiaXNzIjoiYjcwNjUyOTYtY2M0OC00YWE0LWI2NWMtNzI4YzYzODNmZWMwIiwianRpIjoiZGYzZTkwNGYtNzY4YS00ODhlLTllY2EtNWRkNzA2N2NhNWRjIiwibmJmIjoxNjkyMDI2NDcwLCJzdWIiOiJiNzA2NTI5Ni1jYzQ4LTRhYTQtYjY1Yy03MjhjNjM4M2ZlYzAifQ.yJdbzVcDIZotiudVvlzgoJF3wWJ5ZSCh4es9Ro7OtnIV8FgeXCnF1QiVOU9Aj2Uth9SYLyXgc09VgHJ9pgzG6CZVWJRDs0uZJAHPq7TBhvLmpr9bYvqHFKC1qpAvw22a
```

#### Example Token Response

```
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8

{
    "access_token": ******,
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "..."
}
```

#### Example JWT

(Also [see in jwt.io](https://jwt.io/#debugger-io?token=eyJhbGciOiJFUzM4NCIsImprdSI6Imh0dHBzOi8vdmVuZG9yc2VydmljZXMuZXBpYy5jb20vaW50ZXJjb25uZWN0LWFtY3VycHJkLW9hdXRoL29hdXRoMi9rZXlzLzIvRTNEOUE5MkNGNTE2QkNEMTVGMjNCMjU0RkNCNTVBNjMiLCJraWQiOiJwLzVwWUo0SjNQVU9MeGhRb0NqZk5KRkNtOE01aTJicVN0bjcvODRGT1B3PSIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJodHRwczovL2VuOWYzMWU1d2kzeG1mcS5tLnBpcGVkcmVhbS5uZXQiLCJleHAiOjE2OTIwMjY2NTAsImlhdCI6MTY5MjAyNjUzMCwiaXNzIjoiYjcwNjUyOTYtY2M0OC00YWE0LWI2NWMtNzI4YzYzODNmZWMwIiwianRpIjoiZGYzZTkwNGYtNzY4YS00ODhlLTllY2EtNWRkNzA2N2NhNWRjIiwibmJmIjoxNjkyMDI2NDcwLCJzdWIiOiJiNzA2NTI5Ni1jYzQ4LTRhYTQtYjY1Yy03MjhjNjM4M2ZlYzAifQ.yJdbzVcDIZotiudVvlzgoJF3wWJ5ZSCh4es9Ro7OtnIV8FgeXCnF1QiVOU9Aj2Uth9SYLyXgc09VgHJ9pgzG6CZVWJRDs0uZJAHPq7TBhvLmpr9bYvqHFKC1qpAvw22a)).

```
{
    "alg": "ES384",
    "jku": "https://vendorservices.epic.com/interconnect-amcurprd-oauth/oauth2/keys/2/E3D9A92CF516BCD15F23B254FCB55A63",
    "kid": "p/5pYJ4J3PUOLxhQoCjfNJFCm8M5i2bqStn7/84FOPw=",
    "typ": "JWT"
}
.
{
    "aud": "https://en9f31e5wi3xmfq.m.pipedream.net",
    "exp": 1692026650,
    "iat": 1692026530,
    "iss": "b7065296-cc48-4aa4-b65c-728c6383fec0",
    "jti": "df3e904f-768a-488e-9eca-5dd7067ca5dc",
    "nbf": 1692026470,
    "sub": "b7065296-cc48-4aa4-b65c-728c6383fec0"
}
```

#### Example JKU Request

```
GET https://app.example.com/oauth2/keys/2/480786374A752C71B56B83471AAFEDFF
```

#### Example JKU Response

```
HTTP/1.1 200 OK
Cache-Control: public, must-revalidate, max-age=3300,no-store
Content-Length: 258
Content-Type: application/json; charset=utf-8
Access-Control-Allow-Credentials: true
Access-Control-Allow-Headers: origin, authorization, accept, content-type, x-requested-with, prefer, Epic-User-ID, Epic-User-IDType, Epic-Client-ID, soapaction, Epic-MyChartUser-ID, Epic-MyChartUser-IDType
Access-Control-Allow-Methods: GET, HEAD, POST, PUT, DELETE, TRACE, OPTIONS
Access-Control-Allow-Origin: *

{
    "keys": [{
        "kty": "EC",
        "kid": "iTqXXI0zbAnJCKDaobfhkM1f-6rMSpTfyZMRp_2tKI8",
        "crv": "P-521",
        "x": "C1uWSXj2czCDwMTLWV5BFmwxdM6PX9p-Pk9Yf9rIf374m5XP1U8q79dBhLSIuaoj",
        "y": "svOT39UUcPJROSD1FqYLued0rXiooIii1D3jaW6pmGVJFhodzC31cy5sfOYotrzF",
        "alg": "ES512",
        "use": "sig"
    },
    {
        "kty": "EC",
        "crv": "P-521",
        "x": "AUbOrsnOvzbJUmtX75Y4gAK9R5xwlRYn_u1SD1FiN6qkR82Nn0fUB_Am07fTQDCY9tXGIC3U-zu0OaWEHIuXRU1s",
        "y": "ANBHM0k9mOY_Rpq8N7rr-UE7NQfZ4OhIMvB1vhbELskVHzqo-sdoP39kppS105uKRopnGc7oHyZY8xjkO-REMw5L",
        "use": "sig",
        "alg": "ES512",
        "kid": "EwWsxS6/Fedwvbx2qTE3qx++7h9N6bA4Q0RbRNg2bkA="
    }]
}
```

### OAuth 2.0 Specification

Per Breaking Change Notification Q-7365177, to enhance security in Epic back-end integrations,  **all apps that use
backend OAuth 2.0** will soon be required to host their public keys at
a [JWK Set URL (JKU)](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-URLS) instead of uploading a static
key to each environment. This requirement does not apply to apps that do not have the "Backend Systems" user type. Refer
to the following table for Epic versions when this requirement will take effect.

|                         |                                                                                                                                                                                                  |                                                                                                                                                                                                  |
|-------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Epic Version**        | **Epic Sandbox JKU Support**                                                                                                                                                                     | **Epic Customer JKU Support**                                                                                                                                                                    |
| February 2025 and prior | _Optional_                                                                                                                                                                                       | _Optional_ - Each customer can choose to enforce JKU support for backend OAuth apps. This enforcement is off by default.                                                                         |
| August 2025             | Vendor Services and Epic on FHIR no longer allow new static key uploads for use in the sandbox.                                                                                                  |                                                                                                                                                                                                  |
| February 2026           | Vendor Services and Epic on FHIR websites no longer allow static key uploads for new apps or new customer download requests. Both sites still support rotating keys for existing live customers. | Vendor Services and Epic on FHIR websites no longer allow static key uploads for new apps or new customer download requests. Both sites still support rotating keys for existing live customers. |
| May 2026                |                                                                                                                                                                                                  | Static keys are no longer supported in customer environments for backend OAuth.                                                                                                                  |

# Using OAuth 2.0

Applications must secure and protect the privacy of patients and their data. To help meet this objective, Epic supports
using the OAuth 2.0 framework to authenticate and authorize applications.

## Why OAuth 2.0?

OAuth 2.0 enables you to develop your application without having to build a credential management system. Instead of
exposing login credentials to your application, your application and an EHR's authorization server exchange a series of
authorization codes and access tokens. Your application can access protected patient data stored in an EHR's database
after it obtains authorization from the EHR's authorization server.

## Before You Get Started

To use OAuth 2.0 to authorize your application's access to patient information, some information needs to be shared
between the authorization server and your application:

1. `client_id`: The `client_id` identifies your application to authentication servers within the Epic community and
   allows you to connect to any organization.
2. `redirect_uri`: The `redirect_uri` confirms your identity and is used to validate and redirect authentication
   requests that originate from your application. Epic's implementation allows for multiple `redirect_uri`s.
    - **Note** that a `redirect_uri` is not needed for backend services using the client_credentials grant type.
    - An https protocol is required for use in a production environment, but http protocol-based redirect_uris are
      allowed for development and testing.
    - Registered redirect_uris must not contain anything in the fragment (i.e. anything after #).
3. Credentials: Some apps, sometimes referred to as confidential clients, can use credentials registered for a given EHR
   system to obtain authorization to access the system without a user or a patient implicitly or explicitly authorizing
   the app. Examples of this are apps that use refresh tokens to allow users to launch the app outside of an Epic client
   without needing to log in every time they use the app, and backend services that need to access resources without a
   specific person launching the app, for example, fetching data on a scheduled basis.

You can register your application for access to both the sandbox and Epic
organizations [here](https://fhir.epic.com/Developer/Apps). You'll provide information to us, including one or more 
`redirect_uri`s, and Epic will generate a `client_id` for you.

Apps can be launched from within an existing EHR or patient portal session, which is called an EHR launch.
Alternatively, apps can be launched standalone from outside of an existing EHR session. Apps can also be backend
services where no user is launching the app.

**EHR launch (SMART on FHIR)**: The app is launched by the EHR calling a launch URL specified in the EHR's
configuration. The EHR launches the launch URL and appends a launch token and the FHIR server's endpoint URL (ISS
parameter) in the query string. The app exchanges the launch token, along with the client identification parameters to
get an authorization code and eventually the access token.

- See the Embedded Launch section of this guide for more details.

**Standalone launch**: The app launches directly to the authorize endpoint outside of an EHR session and requests
context from the EHR's authorization server.

- See the Standalone Launch section of this guide for more details.

**Backend services**: The app is not authorized by a specific person and likely does not have a user interface, and
therefore calls EHR web services with system-level authorization.

- See the Backend Services section of this guide for more details.

**Desktop integrations through Subspace**: The app requests access to APIs directly available on the EHR's desktop
application via a local HTTP server.

- See
  the [Subspace Communication Framework specification](https://open.epic.com/Tech/TechSpec?spec=Subspace%20Communication%20Framework%20Overview.pdf)
   for more details.

Starting in the August 2019 version of Epic, the Epic implementation of OAuth 2.0 supports PKCE and Epic recommends
using PKCE for native mobile app integrations. For earlier versions or in cases where PKCE cannot be used, Epic
recommends the use of Universal Links/App Links in lieu of custom redirect URL protocol schemes.

The app you build will list both a production Client ID and a non-production Client ID. While testing in the Epic on
FHIR sandbox, use the  **non-production Client ID**.

The base URL for the Current Sandbox environment is:

```
https://fhir.epic.com/interconnect-fhir-oauth/
```

# EHR Launch (SMART on FHIR)[](https://fhir.epic.com/Documentation?docId=oauth2&section=EmbeddedOauth2Launch "Copy a link to this section to your clipboard.")

**Contents**

- Step 1: Your Application is Launched from the Patient Portal or EHR
- Step 2: Your Application Retrieves the Conformance Statement or SMART Configuration
    - Additional Header Requirements
- Step 3: Your Application Requests an Authorization Code
- Step 4: EHR's Authorization Server Reviews The Request
- Step 5: Your Application Exchanges the Authorization Code for an Access Token
    - Non-confidential Clients
    - Frontend Confidential Clients
- OpenID Connect id_tokens
- Validating the OpenID Connect JSON Web Token
- Step 6: Your Application Uses FHIR APIs to Access Patient Data
- Step 7: Use a Refresh Token to Obtain a New Access Token

## How It Works

The app is launched by the EHR calling a launch URL specified in the EHR's configuration. The EHR launches the launch
URL and appends a launch token and the FHIR server's endpoint URL (ISS parameter) in the query string. The app exchanges
the launch token, along with the client identification parameters to get an authorization code and eventually the access
token.

![](https://fhir.epic.com/Content/images/OAuth2/EHR_Launch.png)

### Step 1: Your Application is Launched from the Patient Portal or EHR[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Initial-Launch "Copy a link to this section to your clipboard.")

Your app is launched by the EHR calling the launch URL which is specified in the EHR's configuration. The EHR sends a
launch token and the FHIR server's endpoint URL (ISS parameter).

_Note:_ If you are writing a web-based application that you intend to embed in Epic, you may find that your application
may require minor modifications. We offer a
simple [testing harness](https://open.epic.com/Tech/TechSpec?spec=TestingHarness.zip&specType=tools) to help you
validate that your web application is compatible with the embeddable web application viewer in Epic. As embedding your
application in Hyperspace may not behave the same as presenting it in a browser, ensure that your app works with the
testing harness and that you can run multiple instances of the app at the same time within the same session.

- `launch`: This parameter is an EHR generated token that signifies that an active EHR session already exists. This
  token is one-time use and will be exchanged for the authorization code.
    - See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling launch codes.
- `iss`: This parameter contains the EHR's FHIR endpoint URL, which an app can use to find the EHR's authorization
  server.
    - Your application should create an allowlist of iss values (AKA resource servers) to protect
      against [phishing attacks](https://datatracker.ietf.org/doc/html/rfc6819#section-4.6.4). Rather than accepting any
      iss value in your application, only accept those you know belong to the organizations you integrate with.

### Step 2: Your Application Retrieves the Conformance Statement or SMART Configuration[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Conformance-Statement "Copy a link to this section to your clipboard.")

To determine which authorize and token endpoints to use in the EHR launch flow, you should make a GET request to the
metadata endpoint which is constructed by taking the iss provided and appending /metadata. Alternatively, when
communicating with Epic organizations using the August 2021 version or Epic or later, you can make a GET request to the
SMART configuration endpoint by taking the `iss` and appending /.well-known/smart-configuration.

If no Accept header is sent, the metadata response is XML-formatted and the smart-configuration response is
JSON-formatted. An Accept header can be sent with a value of application/json, application/xml, and the metadata
endpoint additionally supports application/fhir+json and application/json+fhir to specify the format of the response.

**Metadata Example**

Here's an example of what a full metadata request might look like.

```
GET https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/DSTU2/metadata HTTP/1.1
Accept: application/fhir+json
```

Here is an example of what the authorize and token endpoints would look like in the metadata response.

```
"extension": [
{
"url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
"extension": [
  {
    "url": "authorize",
    "valueUri": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize"
  },
  {
    "url": "token",
    "valueUri": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"
  }
]
}
]
```

**Metadata Example with Dynamic Client Registration (starting in the November 2021 version of Epic)**

Here's an example of what a full metadata request might look like. By including an Epic Client ID, clients authorized to
perform dynamic client registration can then determine which endpoint to use when performing dynamic client
registration. Note that this capability is only supported for STU3 and R4 requests.

```
GET https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/metadata HTTP/1.1
Accept: application/fhir+json
Epic-Client-ID: 0000-0000-0000-0000-0000
```

Here is an example of what the authorize, token, and dynamic registration endpoints would look like in the metadata
response.

```
"extension": [
{
"url": "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris",
"extension": [
  {
    "url": "authorize",
    "valueUri": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize"
  },
  {
    "url": "token",
    "valueUri": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"
  },
  {
    "url": "register",
    "valueUri": https://fhir.epic.com/interconnect-fhir-oauth/oauth2/register
  }
]
}
]
```

Here's an example of what a smart-configuration request might look like.

```
GET https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/.well-known/smart-configuration HTTP/1.1
Accept: application/json
```

Here is an example of what the authorize and token endpoints would look like in the smart-configuration response.

```
"authorization_endpoint": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize",
"token_endpoint": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token",
"token_endpoint_auth_methods_supported": [
    "client_secret_post",
    "client_secret_basic",
    "private_key_jwt"
]
```

### Retrieving Conformance Statement: Additional Header Requirements[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Conformance-Statement_Header-Requirements "Copy a link to this section to your clipboard.")

In some cases when making your request to the metadata endpoint, you'll also need to include additional headers to
ensure the correct token and authorize endpoints are returned. This is because in Epic, it is possible to configure the
system such that the FHIR server's endpoint URL, provided in the iss parameter, is overridden on a per client ID basis.
If you do not send the Epic-Client-ID HTTP header with the appropriate production or non-production client ID associated
with your application in your call to the metadata endpoint, you can receive the wrong token and authorize endpoints.
Additional CORS setup might be necessary on the Epic community member's Interconnect server to allow for this header.

Starting in the November 2021 version of Epic, by including an Epic Client ID, clients authorized to perform dynamic
client registration can then determine which endpoint to use when performing dynamic client registration. Note that this
capability is supported for only STU3 and R4 requests.

Here's an example of what a full metadata request might look like with the additional Epic-Client-ID header.

```
GET https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/metadata HTTP/1.1
Accept: application/fhir+json
Epic-Client-ID: 0000-0000-0000-0000-0000
```

### Step 3: Your Application Requests an Authorization Code[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Request_Auth_Code "Copy a link to this section to your clipboard.")

Your application now has a `launch` token obtained from the initial EHR launch as well as the authorize endpoint
obtained from the metadata query. To exchange the launch token for an authorization code, your app needs to either make
an HTTP GET or POST request to the authorize endpoint that contains the parameters below. POST requests are supported
for EHR launches in Epic version November 2020 and later, and are not currently supported for standalone launches.

For POST requests, the parameters should be in the body of the request and should be sent as Content-Type
"application/x-www-form-urlencoded". For GET requests, the parameters should be appended in the querystring of the
request. It is Epic's recommendation to use HTTP POST for EHR launches to overcome any size limits on the request URL.

In either case, the application must redirect the User-Agent (the user's browser) to the authorization server by either
submitting an HTML Form to the authorization server (for HTTP POST) or by redirecting to a specified URL (for HTTP GET).
The request should contain the following parameters.

- `response_type`: This parameter must contain the value "code".
- `client_id`: This parameter contains your web application's client ID issued by Epic.
- `redirect_uri`: This parameter contains your application's redirect URI. After the request completes on the Epic
  server, this URI will be called as a callback. The value of this parameter needs to be URL encoded. This URI must also
  be registered with the EHR's authorization server by adding it to your app listing.
- `scope`: This parameter describes the information for which the web application is requesting access. Starting with
  the Epic 2018 version, a scope of "launch" is required for EHR launch workflows. Starting with the November 2019
  version of
  Epic, [OpenID Connect scopes](http://hl7.org/fhir/smart-app-launch/1.0.0/scopes-and-launch-context/index.html#scopes-for-requesting-identity-data)
   are supported, specifically the "openid" scope is supported for all apps and the "fhirUser" scope is supported by
  apps with the R4 (or greater) SMART on FHIR version selected.
- `launch`: This parameter is required for EHR launch workflows. The value to use will be passed from the EHR.
- `aud`: Starting in the August 2021 version of Epic, health care organizations can optionally configure their system to
  require the aud parameter for EHR launch workflows if a launch context is included in the scope parameter. Starting in
  the May 2023 version of Epic, this parameter will be required. The value to use is the FHIR base URL of the resource
  server the application intends to access, which is typically the FHIR server returned by the iss.
- `state`: This optional parameter is generated by your app and is opaque to the EHR. The EHR's authorization server
  will append it to each subsequent exchange in the workflow for you to validate session integrity. While not required,
  this parameter is recommended to be included and validated with each exchange in order to increase security. For more
  information see [RFC 6819 Section 3.6](https://tools.ietf.org/html/rfc6819#section-3.6).
    - **Note:** Large `state` values in combination with launch tokens that are JWTs (see above) may cause the query
      string for the HTTP GET request to the authorization endpoint to exceed the Epic community member web server's max
      query string length and cause the request to fail. You can mitigate this risk by:
        - Following
          the [SMART App Launch Framework's](http://hl7.org/fhir/smart-app-launch/1.0.0/#step-1-app-asks-for-authorization)
           recommendation that the `state` parameter be an unpredictable value ... with at least 122 bits of entropy
          (e.g., a properly configured random uuid is suitable), and not store any actual application state in the 
          `state` parameter.
        - Using a POST request. If you use this approach, note that you might still need to account for a large state
          value in the HTTP GET redirect back to your own server.

Additional parameters for native mobile apps (available starting in the August 2019 version of Epic):

- `code_challenge`: This optional parameter is generated by your app and used for PKCE. This is the S256 hashed version
  of the code_verifier parameter, which will be used in the token request.
- `code_challenge_method`: This optional parameter indicates the method used for the code_challenge parameter and is
  required if using that parameter. Currently, only the S256 method is supported.

Here's an example of an authorization request using HTTP GET. You will replace the  **[redirect_uri]**,  **[client_id]**
,  **[launch_token]**,  **[state]**,  **[code_challenge]**, and  **[audience]** placeholders with your own values.

```
https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize?scope=launch&response_type=code&redirect_uri=[redirect_uri]&client_id=[client_id]&launch=[launch_token]&state=[state]&code_challenge=[code_challenge]&code_challenge_method=S256&aud=[audience]
```

This is an example HTTP GET from the SMART on FHIR launchpad:

```
https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize?scope=launch&response_type=code&redirect_uri=https%3A%2F%2Ffhir.epic.com%2Ftest%2Fsmart&client_id=d45049c3-3441-40ef-ab4d-b9cd86a17225&launch=GwWCqm4CCTxJaqJiROISsEsOqN7xrdixqTl2uWZhYxMAS7QbFEbtKgi7AN96fKc2kDYfaFrLi8LQivMkD0BY-942hYgGO0_6DfewP2iwH_pe6tR_-fRfiJ2WB_-1uwG0&state=abc123&aud=https%3A%2F%2Ffhir.epic.com%2Finterconnect-fhir-oauth%2Fapi%2Ffhir%2Fdstu2
```

And here's an example of what the same request would look like as an HTTP Post:

```
POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize HTTP/1.1
Content-Type: application/x-www-form-urlencoded

scope=launch&response_type=code&redirect_uri=https%3A%2F%2Ffhir.epic.com%2Ftest%2Fsmart&client_id=d45049c3-3441-40ef-ab4d-b9cd86a17225&launch=GwWCqm4CCTxJaqJiROISsEsOqN7xrdixqTl2uWZhYxMAS7QbFEbtKgi7AN96fKc2kDYfaFrLi8LQivMkD0BY-942hYgGO0_6DfewP2iwH_pe6tR_-fRfiJ2WB_-1uwG0&state=abc123&aud=https%3A%2F%2Ffhir.epic.com%2Finterconnect-fhir-oauth%2Fapi%2Ffhir%2Fdstu2 
```

### Step 4: EHR's Authorization Server Reviews the Request[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Redirect "Copy a link to this section to your clipboard.")

The EHR's authorization server reviews the request from your application. If approved, the authorization server
redirects the browser to the redirect URL supplied in the initial request and appends the following querystring
parameter.

- `code`: This parameter contains the authorization code generated by Epic, which will be exchanged for the access token
  in the next step.
    - See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling authorization codes.
- `state`: This parameter will have the same value as the earlier state parameter. For more information, refer to Step
  3.

Here's an example of what the redirect will look like if Epic's authorization server accepts the request:

```
https://fhir.epic.com/test/smart?code=yfNg-rSc1t5O2p6jVAZLyY00uOOte5KM1y3YUxqsJQnBKEMNsYqOPTyVqcCH3YXaPkLztO9Rvf7bhLqQTwALHcHN6raxpTbR1eVgV2QyLA_4K0HrJO92et3qRXiXPkj7&state=abc123
```

### Step 5: Your Application Exchanges the Authorization Code for an Access Token[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Access-Token-Request "Copy a link to this section to your clipboard.")

After receiving the authorization code, your application trades the code for a JSON object containing an access token
and contextual information by sending an HTTP POST to the token endpoint using a Content-Type header with value of
"application/x-www-form-urlencoded". For more information,
see [RFC 6749 section 4.1.3](https://tools.ietf.org/html/rfc6749#section-4.1.3).

### Access Token Request: Non-confidential Clients[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Access-Token-Request-no-confidential-client "Copy a link to this section to your clipboard.")

The following parameters are required in the POST body:

- `grant_type`: For the EHR launch flow, this should contain the value "authorization_code".
- `code`: This parameter contains the authorization code sent from Epic's authorization server to your application as a
  querystring parameter on the redirect URI as described above.
- `redirect_uri`: This parameter must contain the same redirect URI that you provided in the initial access request. The
  value of this parameter needs to be URL encoded.
- `client_id`: This parameter must contain the application's client ID issued by Epic that you provided in the initial
  request.
- `code_verifier`: This optional parameter is used to verify against your code_challenge parameter when using PKCE. This
  parameter is passed as free text and must match the code_challenge parameter used in your authorization request once
  it is hashed on the server using the code_challenge_method. This parameter is available starting in the August 2019
  version of Epic.

Here's an example of what an HTTP POST request for an access token might look like:

```
POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&code=yfNg-rSc1t5O2p6jVAZLyY00uOOte5KM1y3YUxqsJQnBKEMNsYqOPTyVqcCH3YXaPkLztO9Rvf7bhLqQTwALHcHN6raxpTbR1eVgV2QyLA_4K0HrJO92et3qRXiXPkj7&redirect_uri=https%3A%2F%2Ffhir.epic.com%2Ftest%2Fsmart&client_id=d45049c3-3441-40ef-ab4d-b9cd86a17225 
```

The authorization server responds to the HTTP POST request with a JSON object that includes an access token. The
response contains the following fields:

- `access_token`: This parameter contains the access token issued by Epic to your application and is used in future
  requests.
    - See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling access tokens.
- `token_type`: In Epic's OAuth 2.0 implementation, this parameter always includes the value `bearer`.
- `expires_in`: This parameter contains the number of seconds for which the access token is valid.
- `scope`: This parameter describes the access your application is authorized for.
- `id_token`: Returned only for applications that have requested an  _openid_ scope. See below for more info on OpenID
  Connect id_tokens. This parameter follows the guidelines of
  the [OpenID Connect (OIDC) Core 1.0 specification](https://openid.net/specs/openid-connect-core-1_0.html#IDToken). It
  is signed but not encrypted.
- `patient`: This parameter identifies provides the FHIR ID for the patient, if a patient is in context at time of
  launch.
- `epic.dstu2.patient`: This parameter identifies the DSTU2 FHIR ID for the patient, if a patient is in context at time
  of launch.
- `encounter`: This parameter identifies the FHIR ID for the patient's encounter, if in context at time of launch. The
  encounter token corresponds to the FHIR Encounter resource.
- `location` : This parameter identifies the FHIR ID for the encounter department, if in context at time of launch. The
  location token corresponds to the FHIR Location resource.
- `appointment`: This parameter identifies the FHIR ID for the patient's appointment, if appointment context is
  available at time of launch. The appointment token corresponds to the FHIR Appointment resource.
- `loginDepartment`: This parameter identifies the FHIR ID of the user's login department for launches from Hyperspace.
  The loginDepartment token corresponds to the FHIR Location resource.
- `state`: This parameter will have the same value as the earlier state parameter. For more information, refer to Step
  3.

Note that you can include additional fields in the response if needed based on the integration configuration. For more
information, refer to the Launching your app topic. Here's an example of what a JSON object including an access token
might look like:

```
{
"access_token": "Nxfve4q3H9TKs5F5vf6kRYAZqzK7j9LHvrg1Bw7fU_07_FdV9aRzLCI1GxOn20LuO2Ahl5RkRnz-p8u1MeYWqA85T8s4Ce3LcgQqIwsTkI7wezBsMduPw_xkVtLzLU2O",
"token_type": "bearer",
"expires_in": 3240,
"scope": "openid Patient.read Patient.search ",
"id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IktleUlEIiwidHlwIjoiSldUIn0.eyJhdWQiOiJDbGllbnRJRCIsImV4cCI6RXhwaXJlc0F0LCJpYXQiOklzc3VlZEF0LCJpc3MiOiJJc3N1ZXJVUkwiLCJzdWIiOiJFbmRVc2VySWRlbnRpZmllciJ9",
"__epic.dstu2.patient": "T1wI5bk8n1YVgvWk9D05BmRV0Pi3ECImNSK8DKyKltsMB",
"patient": "T1wI5bk8n1YVgvWk9D05BmRV0Pi3ECImNSK8DKyKltsMB",
"appointment": "eVa-Ad1SCIenVq8CYQVxfKwGtP-DfG3nIy9-5oPwTg2g3",
"encounter": "eySnzekbpf6uGZz87ndPuRQ3",
"location": "e4W4rmGe9QzuGm2Dy4NBqVc0KDe6yGld6HW95UuN-Qd03",
"loginDepartment": "e4W4rmGe9QzuGm2Dy4NBqVc0KDe6yGld6HW95UuN-Qd03",
"state": "abc123",
}
```

At this point, authorization is complete and the web application can access the protected patient data it requests using
FHIR APIs.

### Access Token Request: Frontend Confidential Clients[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Access-Token-Request-confidential-client "Copy a link to this section to your clipboard.")

Consult Standalone Launch: Access Token Request for Frontend Confidential Clients for details on how to obtain an access
token if your app is a confidential client.

### OpenID Connect id_tokens[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_ID-Tokens "Copy a link to this section to your clipboard.")

Epic started supporting the OpenID Connect id_token in the access token response for apps that request the  _openid_
 scope since the November 2019 version of Epic.

A decoded OpenID Connect id_token JWT will have these headers:

| Header | Description                                                                                                            |
|--------|------------------------------------------------------------------------------------------------------------------------|
| `alg`  | The JWT authentication algorithm. Currently only RSA 256 is supported in id_token JWTs so this will always be `RS256`. |
| `typ`  | This is always set to `JWT`.                                                                                           |
| `kid`  | The base64 encoded SHA-256 hash of the public key.                                                                     |

A decoded OpenID Connect id_token JWT will have this payload:

| Claim                | Description                                                                                                                                                                                                                                         | Remarks                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
|----------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `iss`                | Issuer of the JWT. This is set to the token endpoint that should be used by the client.                                                                                                                                                             | Starting in the May 2020 version of Epic, the `iss` will be set to the OAuth 2.0 server endpoint, e.g. **https://<Interconnect Server URL>/oauth2**. For Epic versions prior to May 2020, it is set to the OAuth 2.0 token endpoint, e.g. **https://<Interconnect Server URL>/oauth2/token**.                                                                                                                                                                                                                                                                                                                                       |
| `sub`                | STU3+ FHIR ID for the resource representing the user launching the app.                                                                                                                                                                             | For Epic integrations, the `sub` and `fhirUser` claims reference one of the following resources depending on the type of workflow:<br><br>- Provider/user workflows: Practitioner resource<br>- MyChart self access workflows: Patient resource<br>- MyChart proxy access workflows: RelatedPerson resource<br><br>_**Note** apps with a SMART on FHIR version of DSTU2 will still get a STU3+ FHIR FHIR ID for the user. The STU3+ Practitioner.Read or RelatedPerson.Read APIs would be required to read the Practitioner or RelatedPerson FHIR IDs. Patient FHIR IDs can be used across FHIR versions in Epic's implementation._ |
| `fhirUser`           | Absolute reference to the FHIR resource representing the user launching the app. See the [HL7 documentation](http://hl7.org/fhir/smart-app-launch/1.0.0/scopes-and-launch-context/index.html#scopes-for-requesting-identity-data) for more details. | The _fhirUser_ claim will only be present if app has the R4 (or greater) SMART on FHIR version selected, and requests both the _openid_ and _fhirUser_ scopes in the request to the authorize endpoint. See the remark for the `sub` claim above for more information about what the resource returned in these claims represents.                                                                                                                                                                                                                                                                                                  |
| `aud`                | Audiences that the ID token is intended for. This will be set to the client ID for the application that was just authorized during the SMART on FHIR launch.                                                                                        |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `iat`                | Time integer for when the JWT was created, expressed in seconds since the "Epoch" (1970-01-01T00:00:00Z UTC).                                                                                                                                       |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `exp`                | Expiration time integer for this authentication JWT, expressed in seconds since the "Epoch" (1970-01-01T00:00:00Z UTC).                                                                                                                             | This is set to the current time plus 5 minutes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `preferred_username` | The user's LDAP/AD down-level logon name.                                                                                                                                                                                                           | This claim is supported in Epic version February 2021 and later. The _preferred_username_ claim will be present only if the app requests both the _openid_ and _profile_ scopes in the request to the authorize endpoint.                                                                                                                                                                                                                                                                                                                                                                                                           |
| `nonce`              | The nonce for this client session.                                                                                                                                                                                                                  | The _nonce_ claim will be present only if it was specified in the authorization request. This is an identifier for the client session. For more information about the nonce, refer to [the Authentication Request section of the OpenID specification](https://openid.net/specs/openid-connect-core-1_0.html#AuthRequest).                                                                                                                                                                                                                                                                                                          |

Here is an example decoded `id_token` that could be returned if the app has the R4 (or greater) SMART on FHIR version
selected,  **and** requests both the  _openid_ and  _fhirUser_ scopes in the request to the authorize endpoint:

{
"alg": "RS256",
"kid": "liCulTIaitUzjfUh2AqNiMro47X9HcVcd9XPi8LDJKA=",
"typ": "JWT"
} {
"aud": "de5dae1a-4317-4c25-86f1-ed558e85529b",
"exp": 1595956317,
"fhirUser": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/api/FHIR/R4/Practitioner/exfo6E4EXjWsnhA1OGVElgw3",
"iat": 1595956017,
"iss": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2",
"sub": "exfo6E4EXjWsnhA1OGVElgw3"
}

Here is an example decoded `id_token` that could be returned if the app requests at least the  _openid_ scope in the
request to the authorize endpoint, but either doesn't request the  _fhirUser_ claim or doesn't meet the criteria
outlined above for receiving the  _fhirUser_ claim:

{
"alg": "RS256",
"kid": "liCulTIaitUzjfUh2AqNiMro47X9HcVcd9XPi8LDJKA=",
"typ": "JWT"
} {
"aud": "de5dae1a-4317-4c25-86f1-ed558e85529b",
"exp": 1595956317,
"iat": 1595956017,
"iss": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2",
"sub": "exfo6E4EXjWsnhA1OGVElgw3"
}

### Validating the OpenID Connect JSON Web Token[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Validating-OIDC-ID-Token "Copy a link to this section to your clipboard.")

When completing the OAuth 2.0 authorization workflow with the openid scope, the JSON Web Token (JWT) returned in the
id_token field will be cryptographically signed. The public key to verify the authenticity of the JWT can be found at 
**https://<Interconnect Server URL>/api/epic/2019/Security/Open/PublicKeys/530005/OIDC**, for
example [https://fhir.epic.com/interconnect-fhir-oauth/api/epic/2019/Security/Open/PublicKeys/530005/OIDC](https://fhir.epic.com/interconnect-fhir-oauth/api/epic/2019/Security/Open/PublicKeys/530005/OIDC).

Starting in the May 2020 version of Epic, metadata about the server's OpenID Connect configuration, including the 
`jwks_uri` OIDC public key endpoint, can be found at the OpenID configuration endpoint 
**https://<Interconnect Server URL>/oauth2/.well-known/openid-configuration**, for
example [https://fhir.epic.com/interconnect-fhir-oauth/oauth2/.well-known/openid-configuration](https://fhir.epic.com/interconnect-fhir-oauth/oauth2/.well-known/openid-configuration).

Starting in the August 2021 version of Epic, metadata about the server's OpenID Connect configuration can also be found
at the FHIR endpoint https://<Interconnect Server URL>/api/FHIR/R4/.well-known/openid-configuration, for
example https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/.well-known/openid-configuration.

### Step 6: Your Application Uses FHIR APIs to Access Patient Data[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Using-Access-Token "Copy a link to this section to your clipboard.")

With a valid access token, your application can now access protected patient data from the EHR database using FHIR APIs.
Queries must contain an Authorization header that includes the access token presented as a Bearer token.

Here's an example of what a valid query looks like:

```
GET https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/DSTU2/Patient/T1wI5bk8n1YVgvWk9D05BmRV0Pi3ECImNSK8DKyKltsMB HTTP/1.1
Authorization: Bearer Nxfve4q3H9TKs5F5vf6kRYAZqzK7j9LHvrg1Bw7fU_07_FdV9aRzLCI1GxOn20LuO2Ahl5RkRnz-p8u1MeYWqA85T8s4Ce3LcgQqIwsTkI7wezBsMduPw_xkVtLzLU2O
```

### Step 7: Use a Refresh Token to Obtain a New Access Token[](https://fhir.epic.com/Documentation?docId=oauth2&section=Embedded-Oauth2-Launch_Using-Refresh-Token "Copy a link to this section to your clipboard.")

Refresh tokens are not typically needed for embedded (SMART on FHIR) launches because users are not required to log in
to Epic during the SMART on FHIR launch process, and the access token obtained from the launch process is typically
valid for longer than the user needs to use the app.

Consult the Standalone Launch: Use a Refresh Token to Obtain a New Access Token for details on how to use a refresh
token to get a new an access token if your app uses refresh tokens.

# Standalone Launch[](https://fhir.epic.com/Documentation?docId=oauth2&section=standaloneOauth2Launch "Copy a link to this section to your clipboard.")

**Contents**

- How It Works
    - Step 1: Your Application Requests an Authorization Code
    - Step 2: EHR's Authorization Server Authenticates the User and Authorizes Access
    - Step 3: Your Application Exchanges the Authorization Code for an Access Token
        - Non-confidential Clients
        - Frontend Confidential Clients
    - Step 4: Your Application Uses FHIR APIs to Access Patient Data
    - Step 5: Use a Refresh Token to Obtain a New Access Token
- Offline Access for Native and Browser-Based Applications
    - Step 1: Get the initial access token you'll use to register a dynamic client
    - Step 2: Register the dynamic client
    - Step 3: Get an access token you can use to retrieve FHIR resources
    - Step 3a: Get an access token using the JWT bearer flow
    - Step 3b: Alternate flow with refresh tokens

## How It Works

The app launches directly to the authorize endpoint outside of an EHR session and requests context from the EHR's
authorization server.

![](https://fhir.epic.com/Content/images/OAuth2/Standalone_Launch.png)

### Step 1: Your Application Requests an Authorization Code[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Request_Auth_Code "Copy a link to this section to your clipboard.")

Your application would like to authenticate the user using the OAuth 2.0 workflow. To initiate this process, your app
needs to link (using HTTP GET) to the authorize endpoint and append the following querystring parameters:

- `response_type`: This parameter must contain the value "code".
- `client_id`: This parameter contains your web application's client ID issued by Epic.
- `redirect_uri`: This parameter contains your application's redirect URI. After the request completes on the Epic
  server, this URI will be called as a callback. The value of this parameter needs to be URL encoded. This URI must also
  be registered with the EHR's authorization server by adding it to your app listing.
- `state`: This optional parameter is generated by your app and is opaque to the EHR. The EHR's authorization server
  will append it to each subsequent exchange in the workflow for you to validate session integrity. While not required,
  this parameter is recommended to be included and validated with each exchange in order to increase security. For more
  information see [RFC 6819 Section 3.6](https://tools.ietf.org/html/rfc6819#section-3.6).
- `scope`: This parameter describes the information for which the web application is requesting access. Starting with
  the November 2019 version of Epic, the "openid" and
  "fhirUser" [OpenID Connect scopes](http://hl7.org/fhir/smart-app-launch/1.0.0/scopes-and-launch-context/index.html#scopes-for-requesting-identity-data)
   are supported. While scope is optional in Epic's SMART on FHIR implementation, it's required for testing in the
  sandbox. Providing scope=openid is sufficient.
- `aud`: Starting in the August 2021 version of Epic, health care organizations can optionally configure their system to
  require the aud parameter for Standalone and EHR launch workflows if a launch context is included in the scope
  parameter. Starting in the May 2023 version of Epic, this parameter will be required. The value to use is the base URL
  of the resource server the application intends to access, which is typically the FHIR server.

Additional parameters for native mobile apps (available starting in the August 2019 version of Epic):

- `code_challenge`: This optional parameter is generated by your app and used for PKCE. This is the S256 hashed version
  of the code_verifier parameter, which will be used in the token request.
- `code_challenge_method`: This optional parameter indicates the method used for the code_challenge parameter and is
  required if using that parameter. Currently, only the S256 method is supported.

Here's an example of an authorization request using HTTP GET. You will replace the  **[redirect_uri]**,  **[client_id]**
,  **[state]**, and  **[audience]** placeholders with your own values.

```
https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize?response_type=code&redirect_uri=[redirect_uri]&client_id=[client_id]&state=[state]&aud=[audience]&scope=[scope]
```

This is an example link:

```
https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize?response_type=code&redirect_uri=https%3A%2F%2Ffhir.epic.com%2Ftest%2Fsmart&client_id=d45049c3-3441-40ef-ab4d-b9cd86a17225&state=abc123&aud=https%3A%2F%2Ffhir.epic.com%2Finterconnect-fhir-oauth%2Fapi%2Ffhir%2Fdstu2&scope=openid
```

### Step 2: EHR's Authorization Server Authenticates the User and Authorizes Access[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Authenticate-User "Copy a link to this section to your clipboard.")

The EHR's authorization server reviews the request from your application, authenticates the user  (sample credentials
found here), and authorizes access. If approved, the authorization server redirects the browser to the redirect URL
supplied in the initial request and appends the following querystring parameter.

- `code`: This parameter contains the authorization code generated by Epic, which will be exchanged for the access token
  in the next step.

- See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling authorization codes.

- `state`: This parameter will have the same value as the earlier state parameter. For more information, refer to Step
  1.

Here's an example of what the redirect will look like if Epic's authorization server accepts the request:

```
https://fhir.epic.com/test/smart?code=yfNg-rSc1t5O2p6jVAZLyY00uOOte5KM1y3YUxqsJQnBKEMNsYqOPTyVqcCH3YXaPkLztO9Rvf7bhLqQTwALHcHN6raxpTbR1eVgV2QyLA_4K0HrJO92et3qRXiXPkj7&state=abc123
```

### Step 3: Your Application Exchanges the Authorization Code for an Access Token[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Access-Token-Request "Copy a link to this section to your clipboard.")

After receiving the authorization code, your application trades the code for a JSON object containing an access token
and contextual information by sending an HTTP POST to the token endpoint using a Content-Type header with value of
"application/x-www-form-urlencoded". For more information,
see [RFC 6749 section 4.1.3](https://tools.ietf.org/html/rfc6749#section-4.1.3).

### Access Token Request: Non-confidential Clients[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Access-Token-Request-no-confidential-client "Copy a link to this section to your clipboard.")

The following parameters are required in the POST body:

- `grant_type`: For the Standalone launch flow, this should contain the value "authorization_code".
- `code`: This parameter contains the authorization code sent from Epic's authorization server to your application as a
  querystring parameter on the redirect URI as described above.
- `redirect_uri`: This parameter must contain the same redirect URI that you provided in the initial access request. The
  value of this parameter needs to be URL encoded.
- `client_id`: This parameter must contain the application's client ID issued by Epic that you provided in the initial
  request.
- `code_verifier`: This optional parameter is used to verify against your code_challenge parameter when using PKCE. This
  parameter is passed as free text and must match the code_challenge parameter used in your authorization request once
  it is hashed on the server using the code_challenge_method. This parameter is available starting in the August 2019
  version of Epic.

Here's an example of what an HTTP POST request for an access token might look like:

```
POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&code=yfNg-rSc1t5O2p6jVAZLyY00uOOte5KM1y3YUxqsJQnBKEMNsYqOPTyVqcCH3YXaPkLztO9Rvf7bhLqQTwALHcHN6raxpTbR1eVgV2QyLA_4K0HrJO92et3qRXiXPkj7&redirect_uri=https%3A%2F%2Ffhir.epic.com%2Ftest%2Fsmart&client_id=d45049c3-3441-40ef-ab4d-b9cd86a17225 
```

The authorization server responds to the HTTP POST request with a JSON object that includes an access token. The
response contains the following fields:

- `access_token`: This parameter contains the access token issued by Epic to your application and is used in future
  requests.

- See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling access tokens.

- `token_type`: In Epic's OAuth 2.0 implementation, this parameter always includes the value `bearer`.
- `expires_in`: This parameter contains the number of seconds for which the access token is valid.
- `scope`: This parameter describes the access your application is authorized for.
- `id_token`: Returned only for applications that have requested an  _openid_ scope. See above for more info on OpenID
  Connect id_tokens. This parameter follows the guidelines described earlier in this document.
- `patient`: For patient-facing workflows, this parameter identifies the FHIR ID for the patient on whose behalf
  authorization to the system was granted.
    - **The patient's FHIR ID is not returned for provider-facing standalone launch workflows.**
- `epic.dstu2.patient`: For patient-facing workflows, this parameter identifies the DSTU2 FHIR ID for the patient on
  whose behalf authorization to the system was granted.
    - **The patient's FHIR ID is not returned for provider-facing standalone launch workflows.**

Note that you can pass additional parameters if needed based on the integration configuration. Here's an example of what
a JSON object including an access token might look like:

```
{
"access_token": "Nxfve4q3H9TKs5F5vf6kRYAZqzK7j9LHvrg1Bw7fU_07_FdV9aRzLCI1GxOn20LuO2Ahl5RkRnz-p8u1MeYWqA85T8s4Ce3LcgQqIwsTkI7wezBsMduPw_xkVtLzLU2O",
"token_type": "bearer",
"expires_in": 3240,
"scope": "Patient.read Patient.search ",
"patient": "T1wI5bk8n1YVgvWk9D05BmRV0Pi3ECImNSK8DKyKltsMB"
}
```

At this point, authorization is complete and the web application can access the protected patient data it requested
using FHIR APIs.

### Access Token Request: Frontend Confidential Clients[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Access-Token-Confidential-Client "Copy a link to this section to your clipboard.")

There are two authentication options available for confidential apps that can keep authentication credentials secret:

- Client Secret Authentication
- JSON Web Token (JWT) Authentication

#### Access Token Request: Frontend Confidential Clients with Client Secret Authentication[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Access-Token-Request_With-Client-Secret "Copy a link to this section to your clipboard.")

After receiving the authorization code, your application trades the code for a JSON object containing an access token
and contextual information by sending an HTTP POST to the token endpoint using a Content-Type header with value of
"application/x-www-form-urlencoded". For more information,
see [RFC 6749 section 4.1.3](https://tools.ietf.org/html/rfc6749#section-4.1.3).

The Epic on FHIR website can generate a client secret (effectively a password) for your app to use to obtain refresh
tokens, and store the hashed secret for you, or Epic community members can upload a client secret hash that you provide
them when they activate your app for their system.  **If you provide community members a client secret hash to upload,
you should use a unique client secret per Epic community member and per environment type (non-production and production)
for each Epic community member.**

The following parameters are required in the POST body:

- `grant_type`: This should contain the value `authorization_code`.
- `code`: This parameter contains the authorization code sent from Epic's authorization server to your application as a
  querystring parameter on the redirect URI as described above.
- `redirect_uri`: This parameter must contain the same redirect URI that you provided in the initial access request. The
  value of this parameter needs to be URL encoded.

**Note:** The `client_id` parameter is not passed in the the POST body if you use client secret authentication, which is
different from the access token request for apps that do not use refresh tokens. You will instead pass an 
`Authorization` HTTP header with `client_id` and `client_secret` URL encoded and passed as a username and password.
Conceptually the `Authorization` HTTP header will have this value: base64 (`client_id`:`client_secret`).

For example, using the following `client_id` and `client_secret`:

**client_id**: d45049c3-3441-40ef-ab4d-b9cd86a17225

**URL encoded client_id**: d45049c3-3441-40ef-ab4d-b9cd86a17225  _Note: base64 encoding Epic's client IDs will have no
effect_

**client_secret**: this-is-the-secret-2/7

**URL encoded client_secret**: this-is-the-secret-2%2F7

Would result in this `Authorization` header:

```
Authorization: Basic base64Encode{d45049c3-3441-40ef-ab4d-b9cd86a17225:this-is-the-secret-2%2F7}
```

or

```
Authorization: Basic ZDQ1MDQ5YzMtMzQ0MS00MGVmLWFiNGQtYjljZDg2YTE3MjI1OnRoaXMtaXMtdGhlLXNlY3JldC0yJTJGNw==
```

Here's an example of what a valid HTTP POST might look like:

```
POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token HTTP/1.1
Content-Type: application/x-www-form-urlencoded 
Authorization: Basic ZDQ1MDQ5YzMtMzQ0MS00MGVmLWFiNGQtYjljZDg2YTE3MjI1OnRoaXMtaXMtdGhlLXNlY3JldC0yJTJGNw==

grant_type=authorization_code&code=yfNg-rSc1t5O2p6jVAZLyY00uOOte5KM1y3YUxqsJQnBKEMNsYqOPTyVqcCH3YXaPkLztO9Rvf7bhLqQTwALHcHN6raxpTbR1eVgV2QyLA_4K0HrJO92et3qRXiXPkj7&redirect_uri=https%3A%2F%2Ffhir.epic.com%2Ftest%2Fsmart
```

The authorization server responds to the HTTP POST request with a JSON object that includes an access token and a
refresh token. The response contains the following fields:

- `refresh_token`: This parameter contains the refresh token issued by Epic to your application and can be used to
  obtain a new access token. For more information on how this works, see Step 5.

- See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling refresh tokens.

- `access_token`: This parameter contains the access token issued by Epic to your application and is used in future
  requests.

- See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling access tokens.

- `token_type`: In Epic's OAuth 2.0 implementation, this parameter always includes the value `bearer`.
- `expires_in`: This parameter contains the number of seconds for which the access token is valid.
- `scope`: This parameter describes the access your application is authorized for.
- `id_token`: Returned only for applications that have requested an  _openid_ scope. See above for more info on OpenID
  Connect id_tokens. This parameter follows the guidelines of
  the [OpenID Connect (OIDC) Core 1.0 specification](https://openid.net/specs/openid-connect-core-1_0.html#IDToken). It
  is signed but not encrypted.
- `patient`: For patient-facing workflows, this parameter identifies the FHIR ID for the patient on whose behalf
  authorization to the system was granted.
    - **The patient's FHIR ID is not returned for provider-facing standalone launch workflows.**
- `epic.dstu2.patient`: For patient-facing workflows, this parameter identifies the DSTU2 FHIR ID for the patient on
  whose behalf authorization to the system was granted.
    - **The patient's FHIR ID is not returned for provider-facing standalone launch workflows.**
- `encounter`: This parameter identifies the FHIR ID for the patient's encounter, if in context at time of launch. The
  encounter token corresponds to the FHIR Encounter resource.
    - **The encounter FHIR ID is not returned for standalone launch workflows.**
- `location`: This parameter identifies the FHIR ID for the ecounter department, if in context at time of launch. The
  location token corresponds to the FHIR Location resource.
    - **The location FHIR ID is not returned for standalone launch workflows.**
- `appointment`: This parameter identifies the FHIR ID for the patient's appointment, if appointment context is
  available at time of launch. The appointment token corresponds to the FHIR Appointment resource.
    - **The appointment FHIR ID is not returned for standalone launch workflows.**
- `loginDepartment`: This parameter identifies the FHIR ID of the user's login department for launches from Hyperspace.
  The loginDepartment token corresponds to the FHIR Location resource.
    - **The loginDepartment FHIR ID is not returned for standalone launch workflows.**

Note that you can pass additional parameters if needed based on the integration configuration. Here's an example of what
a JSON object including an access token and refres token might look like:

```
{
"access_token": "Nxfve4q3H9TKs5F5vf6kRYAZqzK7j9LHvrg1Bw7fU_07_FdV9aRzLCI1GxOn20LuO2Ahl5RkRnz-p8u1MeYWqA85T8s4Ce3LcgQqIwsTkI7wezBsMduPw_xkVtLzLU2O",
"refresh_token": "H9TKs5F5vf6kRYAZqzK7j9L_07_FdV9aRzLCI1GxOn20LuO2Ahl5R1MeYWqA85T8s4sTkI7wezBsMduPw_xkLzLU2O",
"token_type": "bearer",
"expires_in": 3240,
"scope": "Patient.read Patient.search ",
"patient": "T1wI5bk8n1YVgvWk9D05BmRV0Pi3ECImNSK8DKyKltsMB"
}
```

At this point, authorization is complete and the web application can access the protected patient data it requested
using FHIR APIs.

#### Access Token Request: Frontend Confidential Clients with JSON Web Token (JWT) Authentication[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Access-Token-Request_With-JWT "Copy a link to this section to your clipboard.")

After receiving the authorization code, your application trades the code for a JSON object containing an access token
and contextual information by sending an HTTP POST to the token endpoint using a Content-Type header with value of
"application/x-www-form-urlencoded". For more information,
see [RFC 6749 section 4.1.3](https://tools.ietf.org/html/rfc6749#section-4.1.3).

You can use a one-time use [JSON Web Token (JWT)](https://tools.ietf.org/html/rfc7519) to authenticate your app to the
authorization server and obtain an access token and/or refresh token. There are several libraries for creating JWTs.
See [jwt.io](https://jwt.io/) for some examples. You'll pre-register a JSON Web Key Set or JWK Set URL for a given Epic
community member environment on the Epic on FHIR website and then use the corresponding private key to create a signed
JWT. Frontend apps using JWT authentication must use a JSON Web Key Set or JWK Set URL as static public keys are not
supported.

See the Creating a Public Private Key Pair for JWT Signature and the Creating a JWT sections for details on how to
create a signed JWT.

The following parameters are required in the POST body:

- `grant_type`: This should contain the value `authorization_code`.
- `code`: This parameter contains the authorization code sent from Epic's authorization server to your application as a
  querystring parameter on the redirect URI as described above.
- `redirect_uri`: This parameter must contain the same redirect URI that you provided in the initial access request. The
  value of this parameter needs to be URL encoded.
- `client_assertion_type`: This should be set to `urn:ietf:params:oauth:client-assertion-type:jwt-bearer`.
- `client_assertion`: This will be the one-time use JSON Web Token (JWT) your app generated using the steps mentioned
  above.

Here's an example of what a valid HTTP POST might look like:

```
POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&client_assertion_type=urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer&code=yfNg-rSc1t5O2p6jVAZLyY00uOOte5KM1y3YUxqsJQnBKEMNsYqOPTyVqcCH3YXaPkLztO9Rvf7bhLqQTwALHcHN6raxpTbR1eVgV2QyLA_4K0HrJO92et3qRXiXPkj7&redirect_uri=https%3A%2F%2Ffhir.epic.com%2Ftest%2Fsmart&client_assertion=eyJhbGciOiJSUzM4NCIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJkNDUwNDljMy0zNDQxLTQwZWYtYWI0ZC1iOWNkODZhMTcyMjUiLCJzdWIiOiJkNDUwNDljMy0zNDQxLTQwZWYtYWI0ZC1iOWNkODZhMTcyMjUiLCJhdWQiOiJodHRwczovL2ZoaXIuZXBpYy5jb20vaW50ZXJjb25uZWN0LWZoaXItb2F1dGgvb2F1dGgyL3Rva2VuIiwianRpIjoiZjllYWFmYmEtMmU0OS0xMWVhLTg4ODAtNWNlMGM1YWVlNjc5IiwiZXhwIjoxNTgzNTI0NDAyLCJuYmYiOjE1ODM1MjQxMDIsImlhdCI6MTU4MzUyNDEwMn0.dztrzHo9RRwNRaB32QxYLaa9CcIMoOePRCbpqsRKgyJmBOGb9acnEZARaCzRDGQrXccAQ9-syuxz5QRMHda0S3VbqM2KX0VRh9GfqG3WJBizp11Lzvc2qiUPr9i9CqjtqiwAbkME40tioIJMC6DKvxxjuS-St5pZbSHR-kjn3ex2iwUJgPbCfv8cJmt19dHctixFR6OG-YB6lFXXpNP8XnL7g85yLOYoQcwofN0k8qK8h4uh8axTPC21fv21mCt50gx59XgKsszysZnMDt8OG_G4gjk_8JnGHwOVkJhqj5oeg_GdmBhQ4UPuxt3YvCOTW9S2vMikNUnxrhdVvn2GVg

```

The authorization server responds to the HTTP POST request with a JSON object that includes an access token and a
refresh token. The response contains the following fields:

- `refresh_token`: If your app is configured to receive refresh tokens, this parameter contains the refresh token issued
  by Epic to your application and can be used to obtain a new access token. For more information on how this works, see
  Step 5.
    - See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling refresh tokens.
- `access_token`: This parameter contains the access token issued by Epic to your application and is used in future
  requests.
    - See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling access tokens.
- `token_type`: In Epic's OAuth 2.0 implementation, this parameter always includes the value `bearer`.
- `expires_in`: This parameter contains the number of seconds for which the access token is valid.
- `scope`: This parameter describes the access your application is authorized for.
- `id_token`: Returned only for applications that have requested an  _openid_ scope. See above for more info on OpenID
  Connect id_tokens. This parameter follows the guidelines of
  the [OpenID Connect (OIDC) Core 1.0 specification](https://openid.net/specs/openid-connect-core-1_0.html#IDToken). It
  is signed but not encrypted.
- `patient`: For patient-facing workflows, this parameter identifies the FHIR ID for the patient on whose behalf
  authorization to the system was granted.
    - **The patient's FHIR ID is not returned for provider-facing standalone launch workflows.**
- `epic.dstu2.patient`: For patient-facing workflows, this parameter identifies the DSTU2 FHIR ID for the patient on
  whose behalf authorization to the system was granted.
    - **The patient's FHIR ID is not returned for provider-facing standalone launch workflows.**
- `encounter`: This parameter identifies the FHIR ID for the patient's encounter, if in context at time of launch. The
  encounter token corresponds to the FHIR Encounter resource.
    - **The encounter FHIR ID is not returned for standalone launch workflows.**
- `location`: This parameter identifies the FHIR ID for the encounter department, if in context at time of launch. The
  location token corresponds to the FHIR Location resource.
    - **The location FHIR ID is not returned for standalone launch workflows.**
- `appointment`: This parameter identifies the FHIR ID for the patient's appointment, if appointment context is
  available at time of launch. The appointment token corresponds to the FHIR Appointment resource.
    - **The appointment FHIR ID is not returned for standalone launch workflows.**
- `loginDepartment`: This parameter identifies the FHIR ID of the user's login department for launches from Hyperspace.
  The loginDepartment token corresponds to the FHIR Location resource.
    - **The loginDepartment FHIR ID is not returned for standalone launch workflows.**

Note that you can pass additional parameters if needed based on the integration configuration. Here's an example of what
a JSON object including an access token might look like:

```
{
  "access_token": "Nxfve4q3H9TKs5F5vf6kRYAZqzK7j9LHvrg1Bw7fU_07_FdV9aRzLCI1GxOn20LuO2Ahl5RkRnz-p8u1MeYWqA85T8s4Ce3LcgQqIwsTkI7wezBsMduPw_xkVtLzLU2O",
  "refresh_token": "H9TKs5F5vf6kRYAZqzK7j9L_07_FdV9aRzLCI1GxOn20LuO2Ahl5R1MeYWqA85T8s4sTkI7wezBsMduPw_xkLzLU2O",
  "token_type": "bearer",
  "expires_in": 3240,
  "scope": "Patient.read Patient.search ",
  "patient": "T1wI5bk8n1YVgvWk9D05BmRV0Pi3ECImNSK8DKyKltsMB"
}
```

At this point, authorization is complete and the web application can access the protected patient data it requested
using FHIR APIs.

### Step 4: Your Application Uses FHIR APIs to Access Patient Data[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Using-Access-Token "Copy a link to this section to your clipboard.")

With a valid access token, your application can now access protected patient data from the EHR database using FHIR APIs.
Queries must contain an Authorization header that includes the access token presented as a Bearer token.

Here's an example of what a valid query looks like:

```
GET https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/DSTU2/Patient/T1wI5bk8n1YVgvWk9D05BmRV0Pi3ECImNSK8DKyKltsMB HTTP/1.1
Authorization: Bearer Nxfve4q3H9TKs5F5vf6kRYAZqzK7j9LHvrg1Bw7fU_07_FdV9aRzLCI1GxOn20LuO2Ahl5RkRnz-p8u1MeYWqA85T8s4Ce3LcgQqIwsTkI7wezBsMduPw_xkVtLzLU2O
```

### Step 5: Use a Refresh Token to Obtain a New Access Token[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch_Using-Refresh-Token "Copy a link to this section to your clipboard.")

If your app uses refresh tokens (i.e. it can securely store credentials), then you can use a refresh token to request a
new access token when the current access token expires (determined by the `expires_in` field from the authorization
response from step 3).

There are two authentication options available for confidential apps that can keep authentication credentials secret:

- Client Secret Authentication
- JSON Web Token (JWT) Authentication

#### Using Refresh Tokens: If You Are Using Client Secret Authentication[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch-Using-Refresh-Token-Client-Secret "Copy a link to this section to your clipboard.")

Your application trades the `refresh_token` for a JSON object containing a new access token and contextual information
by sending an HTTP POST to the token endpoint using a `Content-Type` HTTP header with value of
"application/x-www-form-urlencoded". For more information,
see [RFC 6749 section 4.1.3](https://tools.ietf.org/html/rfc6749#section-4.1.3).

The Epic on FHIR website can generate a client secret (effectively a password) for your app when using refresh tokens,
and store the hashed secret for you, or Epic community members can upload a client secret hash that you provide them
when they activate your app for their system.  **If you provide community members a client secret hash to upload, you
should use a unique client secret per Epic community member and per environment type (non-production and production) for
each Epic community member.**

The following parameters are required in the POST body:

- `grant_type`: This parameter always contains the value `refresh_token`.
- `refresh_token`: The refresh token received from a prior authorization request.

An `Authorization` header using HTTP Basic Authentication is required, where the username is the URL encoded `client_id`
 and the password is the URL encoded `client_secret`.

For example, using the following `client_id` and `client_secret`:

**client_id**: d45049c3-3441-40ef-ab4d-b9cd86a17225

**URL encoded client_id**: d45049c3-3441-40ef-ab4d-b9cd86a17225  _Note: base64 encoding Epic's client IDs will have no
effect_

**client_secret**: this-is-the-secret-2/7

**URL encoded client_secret**: this-is-the-secret-2%2F7

Would result in this `Authorization` header:

```
Authorization: Basic base64Encode{d45049c3-3441-40ef-ab4d-b9cd86a17225:this-is-the-secret-2%2F7}
```

or

```
Authorization: Basic ZDQ1MDQ5YzMtMzQ0MS00MGVmLWFiNGQtYjljZDg2YTE3MjI1OnRoaXMtaXMtdGhlLXNlY3JldC0yJTJGNw==
```

Here's an example of what a valid HTTP POST might look like:

```
POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token HTTP/1.1
Authorization: Basic ZDQ1MDQ5YzMtMzQ0MS00MGVmLWFiNGQtYjljZDg2YTE3MjI1OnRoaXMtaXMtdGhlLXNlY3JldC0yJTJGNw==
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token=j12xcniournlsdf234bgsd
```

The authorization server responds to the HTTP POST request with a JSON object that includes the new access token. The
response contains the following fields:

- `access_token`: This parameter contains the new access token issued.
- `token_type`: In Epic's OAuth 2.0 implementation, this parameter always includes the value `bearer`.
- `expires_in`: This parameter contains the number of seconds for which the access token is valid.
- `scope`: This parameter describes the access your application is authorized for.

An example response to the previous request may look like the following:

```
{
"access_token": "57CjhZEdiTcCh1nqIwQUw5rODOLP3bSTnMEGNYbBerSeNn8hIUm6Mlc5ruCTfQawjRAoR8sYr8S_7vNdnJfgRKfD6s8mOqPnvJ8vZOHjEvy7l3Ra9frDaEAUBbN-j86k",
"token_type": "bearer",
"expires_in": 3240,
"scope": "Patient.read Patient.search"
}
```

#### Using Refresh Tokens: If You Are Using JSON Web Token Authentication[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-Launch-Using-Refresh-Token-JWT "Copy a link to this section to your clipboard.")

Your application trades the `refresh_token` for a JSON object containing a new access token and contextual information
by sending an HTTP POST to the token endpoint using a `Content-Type` HTTP header with value of
"application/x-www-form-urlencoded". For more information,
see [RFC 6749 section 4.1.3](https://tools.ietf.org/html/rfc6749#section-4.1.3).

You can use a one-time use [JSON Web Token (JWT)](https://tools.ietf.org/html/rfc7519) to authenticate your app to the
authorization server to exchange the refresh token for a new access token. There are several libraries for creating
JWTs. See [jwt.io](https://jwt.io/) for some examples. You'll pre-register a JSON Web Key Set or JWK Set URL for a given
Epic community member environment on the Epic on FHIR website and then use the corresponding private key to create a
signed JWT. Frontend apps using JWT authentication must use a JSON Web Key Set or JWK Set URL as static public keys are
not supported.

See the Creating a Public Private Key Pair for JWT Signature and the Creating a JWT sections for details on how to
create a signed JWT.

The following parameters are required in the POST body:

- `grant_type`: This should contain the value `refresh_token`.
- `refresh_token`: The refresh token received from a prior authorization request.
- `client_assertion_type`: This should be set to `urn:ietf:params:oauth:client-assertion-type:jwt-bearer`.
- `client_assertion`: This will be the one-time use JSON Web Token (JWT) your app generated using the steps mentioned
  above.

Here's an example of what a valid HTTP POST might look like:

```
POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&client_assertion_type=urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer&client_assertion=eyJhbGciOiJSUzM4NCIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJkNDUwNDljMy0zNDQxLTQwZWYtYWI0ZC1iOWNkODZhMTcyMjUiLCJzdWIiOiJkNDUwNDljMy0zNDQxLTQwZWYtYWI0ZC1iOWNkODZhMTcyMjUiLCJhdWQiOiJodHRwczovL2ZoaXIuZXBpYy5jb20vaW50ZXJjb25uZWN0LWZoaXItb2F1dGgvb2F1dGgyL3Rva2VuIiwianRpIjoiZjllYWFmYmEtMmU0OS0xMWVhLTg4ODAtNWNlMGM1YWVlNjc5IiwiZXhwIjoxNTgzNTI0NDAyLCJuYmYiOjE1ODM1MjQxMDIsImlhdCI6MTU4MzUyNDEwMn0.dztrzHo9RRwNRaB32QxYLaa9CcIMoOePRCbpqsRKgyJmBOGb9acnEZARaCzRDGQrXccAQ9-syuxz5QRMHda0S3VbqM2KX0VRh9GfqG3WJBizp11Lzvc2qiUPr9i9CqjtqiwAbkME40tioIJMC6DKvxxjuS-St5pZbSHR-kjn3ex2iwUJgPbCfv8cJmt19dHctixFR6OG-YB6lFXXpNP8XnL7g85yLOYoQcwofN0k8qK8h4uh8axTPC21fv21mCt50gx59XgKsszysZnMDt8OG_G4gjk_8JnGHwOVkJhqj5oeg_GdmBhQ4UPuxt3YvCOTW9S2vMikNUnxrhdVvn2GVg

```

The authorization server responds to the HTTP POST request with a JSON object that includes the new access token. The
response contains the following fields:

- `access_token`: This parameter contains the new access token issued.
- `token_type`: In Epic's OAuth 2.0 implementation, this parameter always includes the value `bearer`.
- `expires_in`: This parameter contains the number of seconds for which the access token is valid.
- `scope`: This parameter describes the access your application is authorized for.

An example response to the previous request may look like the following:

```
{
  "access_token": "57CjhZEdiTcCh1nqIwQUw5rODOLP3bSTnMEGNYbBerSeNn8hIUm6Mlc5ruCTfQawjRAoR8sYr8S_7vNdnJfgRKfD6s8mOqPnvJ8vZOHjEvy7l3Ra9frDaEAUBbN-j86k",
  "token_type": "bearer",
  "expires_in": 3240,
  "scope": "Patient.read Patient.search"
}
```

## Offline Access for Native and Browser-Based Applications[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-OfflineAccess-0 "Copy a link to this section to your clipboard.")

For a native client app (for example, an iOS mobile app or a Windows desktop app) or a browser-based app (for example, a
single-page application) to use
the [confidential app profile](https://hl7.org/fhir/smart-app-launch/1.0.0/#use-the-confidential-app--profile-if-your-app-is-able-to-protect-a-client_secret)
 in the SMART App Launch framework, that app needs to use
"[additional technology (such as dynamic client registration and universal redirect_uris) to protect the secret](https://hl7.org/fhir/smart-app-launch/1.0.0/)."
If you have a native client or browser-based app, you can register a dynamic client to integrate with Epic using the
steps below.

### Step 1: Get the initial access token you'll use to register a dynamic client[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-OfflineAccess-1 "Copy a link to this section to your clipboard.")

Your app uses a flow like the one described above for a Standalone Launch. Note your application will specifically not
be using a client secret, and will use its initial client ID issued by Epic.

The end-result of this flow is your app obtaining an initial access token (defined
in [RFC 7591](https://datatracker.ietf.org/doc/html/rfc7591#section-1.2)) used for registering a client instance (step
2). This token is intentionally one-time-use and should be used for registration immediately.

### Step 2: Register the dynamic client[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-OfflineAccess-2 "Copy a link to this section to your clipboard.")

To register the dynamic client, your application needs to:

1. Generate a public-private key pair on the user’s phone or computer.
    - Native apps can use the same strategies as backend apps to generate key sets
    - Browser-based apps can use the [WebCrypto API](https://w3c.github.io/webcrypto/)
2. Securely store that device-specific key pair on the user’s device.
    - Browser-based apps using WebCrypto should follow key
      storage [recommendations](https://w3c.github.io/webcrypto/#concepts-key-storage)
3. Use the access token from step 1 to register a dynamic client using
   the [OAuth 2.0 Dynamic Client Registration Protocol](https://datatracker.ietf.org/doc/html/rfc7591).

The client ID and private key should be associated with the Epic environment they are communicating with. Identify the
Epic environment with its OAuth 2.0 server URL. If this is a backend integration or a subspace integration, the
environment health system identifier (HSI) can be used to identify the environment instead.

This request must have two elements:

- `software_id`: This parameter must contain the application’s client ID issued by Epic.
- `jwks`: This parameter contains a JSON Web Key Set containing the public key from the key pair your application
  generated in step 2A.

Here’s an example of what an HTTP POST request to register a dynamic client might look like:

```

POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/register HTTP/1.1
Content-Type: application/json
Authorization: Bearer Nxfve4q3H9TKs5F5vf6kRYAZqzK7j9LHvrg1Bw7fU_07_FdV9aRzLCI1GxOn20LuO2Ahl5RkRnz-p8u1MeYWqA85T8s4Ce3LcgQqIwsTkI7wezBsMduPw_xkVtLzLU2O

{
    "software_id": "d45049c3-3441-40ef-ab4d-b9cd86a17225",
    "jwks": { 
        "keys": [{
                "e": "AQAB",
                "kty": "RSA",
                "n": "vGASMnWdI-ManPgJi5XeT15Uf1tgpaNBmxfa-_bKG6G1DDTsYBy2K1uubppWMcl8Ff_2oWe6wKDMx2-bvrQQkR1zcV96yOgNmfDXuSSR1y7xk1Kd-uUhvmIKk81UvKbKOnPetnO1IftpEBm5Llzy-1dN3kkJqFabFSd3ujqi2ZGuvxfouZ-S3lpTU3O6zxNR6oZEbP2BwECoBORL5cOWOu_pYJvALf0njmamRQ2FKKCC-pf0LBtACU9tbPgHorD3iDdis1_cvk16i9a3HE2h4Hei4-nDQRXfVgXLzgr7GdJf1ArR1y65LVWvtuwNf7BaxVkEae1qKVLa2RUeg8imuw",
            }
        ]
    }
}
```

The EHR stores your app's public key, issues it a dynamic client ID, and returns a response that might look like:

```

HTTP/1.1 201 Created
Content-Type: application/json

{
    "redirect_uris": [
        " https://fhir.epic.com/test/smart"
    ],
    "token_endpoint_auth_method": "none",
    "grant_types": [
        "urn:ietf:params:oauth:grant-type:jwt-bearer"
    ],
    "software_id": " d45049c3-3441-40ef-ab4d-b9cd86a17225",
    "client_id": "G65DA2AF4-1C91-11EC-9280-0050568B7514",
    "client_id_issued_at": 1632417134,
    "jwks": {
        "keys": [{
                "kty": "RSA",
                "n": "vGASMnWdI-ManPgJi5XeT15Uf1tgpaNBmxfa-_bKG6G1DDTsYBy2K1uubppWMcl8Ff_2oWe6wKDMx2-bvrQQkR1zcV96yOgNmfDXuSSR1y7xk1Kd-uUhvmIKk81UvKbKOnPetnO1IftpEBm5Llzy-1dN3kkJqFabFSd3ujqi2ZGuvxfouZ-S3lpTU3O6zxNR6oZEbP2BwECoBORL5cOWOu_pYJvALf0njmamRQ2FKKCC-pf0LBtACU9tbPgHorD3iDdis1_cvk16i9a3HE2h4Hei4-nDQRXfVgXLzgr7GdJf1ArR1y65LVWvtuwNf7BaxVkEae1qKVLa2RUeg8imuw",
                "e": "AQAB"
            }
        ]
    }
}
```

### Step 3: Get an access token you can use to retrieve FHIR resources[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-OfflineAccess-3 "Copy a link to this section to your clipboard.")

Now that you have registered a dynamic client with its own credentials, there are two ways you can get an access token
to make FHIR calls.  We recommend that you use the  **JWT bearer flow.** In the JWT bearer flow, the client issues a JWT
with its client ID as the "sub" (subject) claim, indicating the access token should be issued to the users who
registered the client. It is also possible to use the SMART standalone launch sequence again to get a refresh token and
then use the refresh token flow. Note that both flows involve your app making a JSON Web Token and signing it with its
private key.

#### Generate a JSON Web Token

Your app follows the guidelines below to create a JWT assertion using its new private key.

To get an access token, your confidential app needs to authenticate itself.  Your dynamic client uses the private key it
generated in step 2 to sign a JWT.

Here’s an example of the JWT body:

```

{
    "sub": "G00000000-0129-A6FA-7EDC-55B3FB6E85F8",
    "aud": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token",
    "jti": "3fca7b08-e5a1-4476-9e4b-4e17f0ad7d1d",
    "nbf": 1639694983,
    "exp": 1639695283,
    "iat": 1639694983,
    "iss": "G00000000-0129-A6FA-7EDC-55B3FB6E85F8"
}
```

And here’s the final encoded JWT:

```
eyJhbGciOiJSUzI1NiIsImtpZCI6ImJXbGphR0ZsYkNBOE15QnNZWFZ5WVE9PSIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJHMDAwMDAwMDAtMDEyOS1BNkZBLTdFREMtNTVCM0ZCNkU4NUY4IiwiYXVkIjpbImh0dHBzOi8vdnMtaWN4LmVwaWMuY29tL0ludGVyY29ubmVjdC1DdXJyZW50LUZpbmFsLVByaW1hcnkvIl0sImp0aSI6IjNmY2E3YjA4LWU1YTEtNDQ3Ni05ZTRiLTRlMTdmMGFkN2QxZCIsIm5iZiI6MTYzOTY5NDk4MywiZXhwIjoxNjM5Njk1MjgzLCJpYXQiOjE2Mzk2OTQ5ODMsImlzcyI6IkcwMDAwMDAwMC0wMTI5LUE2RkEtN0VEQy01NUIzRkI2RTg1RjgifQ.UyW_--xpDdfB3EbxQ1KwRRuNDGIb034Y9mpExaaYyoVVkfz3ophzueIFMRrcdqTWmH96Ivx7O-fnjvHkb9iv1ELGVS_UYZy-JNb7r5VHDhdEYoRJzomYQzAyZutK9CJuJQZSvJeQLOXFoFN-fuCHIUBmSoTY5FDGy8gmq_a5fD_L-PnrXCD6SyP663s8kP-cFWh1iuXP0pjg8EMuFFEwgSo-chvv6RO4LIBebqkkv5qkgO_nrcXVJpxu42FDNrP2618q2Rw7sdIaewDw_I5026T4jNSoJXT12dHurqzedvC20exOO2MA6Vh1o2iVyzIfPE1EnEf-hk4WRFtQTUlqCQ
```

### Step 3a: Get an access token using the JWT bearer flow[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-OfflineAccess-3a "Copy a link to this section to your clipboard.")

For the duration the user selected when approving your app to get an authorization code in step 1, your app can get an
access token whenever it needs using the JWT bearer authorization grant described
in [RFC 7523 Section 2.1](https://datatracker.ietf.org/doc/html/rfc7523#section-2.1).

Recall that in the JWT bearer flow, the client issues a JWT to itself with a "sub" (subject) claim that indicates the
user on whose behalf the client acts.  Because the dynamic client your app registered in step 2 is bound to the user who
logged in during the SMART standalone launch sequence in step 1, the server  _only_ accepts a JWT where the subject is
the user bound to that client.

Your app posts the JWT it issued itself in the Generate a JSON Web Token step to the server to get an access token. The
following parameters are required in the POST body:

- `grant_type`: This parameter contains the static value `urn:ietf:params:oauth:grant-type:jwt-bearer.`
- `assertion`: This parameter contains the JWT your app generated above.
- `client_id`: This parameter contains the dynamic client ID assigned in step 2.

Here’s an example of an HTTP POST request for an access token. You will replace the  **[assertion]**  **and** 
**[client_id]** placeholders with your own values.

```

POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=[assertion]&client_id=[client_id]
```

The authorization server responds to the HTTP POST request with a JSON object that includes an access token. The
response contains the following fields:

- `access_token`: This parameter contains the access token issued by Epic to your application and is used in future
  requests.

- See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling access tokens.

- `token_type`: In Epic's OAuth 2.0 implementation, this parameter always includes the value bearer.
- `expires_in`: This parameter contains the number of seconds for which the access token is valid.
- `epic.dstu2.patient`: This parameter identifies the DSTU2 FHIR ID for the patient, if a patient is in context at time
  of launch.
- `scope`: This parameter describes the access your application is authorized for.
- `patient`: This parameter identifies provides the FHIR ID for the patient.

Note that you can include additional fields in the response if needed based on the integration configuration. For more
information, refer to Launching your apptopic. Here's an example of what a JSON object including an access token might
look like:

```

{
    "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOiJ1cm46R1RNUUFDVVI6Y2UuZ3RtcWFjdXIiLCJjbGllbnRfaWQiOiJHMDAwMDAwMDAtMDEyOS1BNkZBLTdFREMtNTVCM0ZCNkU4NUY4IiwiZXBpYy5lY2kiOiJ1cm46ZXBpYzpzMmN1cmd0IiwiZXBpYy5tZXRhZGF0YSI6ImVTMUF1dng4NlhRanRmVExub2loeks4QVRjdU5OUDFnRHhCRklvR0EyVUhHdlpDRE5PYjVZQ3NFZVhlUjExa1UyTjBHaHIwZjIwcE5yQnJ0V1JUa0l4d3VoVnlYeEVYUjQwVGlkdXNKdTk5ZmZRLWsybjFJWEY4X2I0SS11a054IiwiZXBpYy50b2tlbnR5cGUiOiJhY2Nlc3MiLCJleHAiOjE2Mzk2OTg1ODgsImlhdCI6MTYzOTY5NDk4OCwiaXNzIjoidXJuOkdUTVFBQ1VSOmNlLmd0bXFhY3VyIiwianRpIjoiYzQ1YzU4NjUtY2E3Ny00YWVlLTk3NDEtY2YzNzFiNDlhY2FkIiwibmJmIjoxNjM5Njk0OTg4LCJzdWIiOiJlRmdDY3E4cW1PeXY1b3FEcUFyUS5MQTMifQ.G0Z3PBv4ldpTqkjBjNUnvRYeVnNzT8qvLsGAVN8S9YibJGGCH_Txd6Ph1c9yB2hlQW3dw9IkaAvxxlUuclGMzmtyPXeo8wcWC07t_0vVasS-Ya9VjeDtR1hO8rcqEgV1DhKZ1jsEbzlRvKuZvONew0gL25ug6dOolNXcPcluzK6sxEyf2UoosX-W3nsU0iYZPJI-mf7lMEbsMUOSY8CR-77uBap3suxxHy03BwtkAXP0GwW0KSjOVe7_bsxX9k4DEhWyZuEOgDjEhONQFe2TeuWgUcI2KQeK5HjzmxN3dp56rCZ8zlhlukgw-C0F2IDbkZ5on7g7rl8lm29I7_kq9g",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "patient/Immunization.Read patient/Immunization.read patient/Patient.read patient/Practitioner.read patient/PractitionerRole.read launch/patient offline_access",
    "state": "oYK9nyFdUgTb1n_hMHZESDea",
    "patient": "eUYSU3eH0lceii-4SYNvpPw3",
    "__epic.dstu2.patient": "TJR7HfYCw58VnihLrp.axehccg-4IcjmZd3lw7Spm1F8B"
}
```

### Step 3b: Alternate flow with refresh tokens[](https://fhir.epic.com/Documentation?docId=oauth2&section=Standalone-Oauth2-OfflineAccess-3b "Copy a link to this section to your clipboard.")

**If you completed Step 3A, you do not need to complete this step. This is an alternate workflow to the JWT bearer flow.
We strongly recommend that you use the JWT bearer flow above so you can provide a smoother user experience in which the
user only needs to authenticate once**. To get a refresh token, your app would need to go through the Standalone Launch
sequence a second time. The first time (see Step 1: Get the access token you'll use to register a dynamic client) you
were using the public app profile and weren't issued a refresh token.  Now that you have registered a dynamic client
with its own credentials, you can use
the [confidential-asymmetric](http://www.hl7.org/fhir/smart-app-launch/client-confidential-asymmetric.html#client-authentication-asymmetric-public-key)
 app profile.

#### Get an authorization code

This is the same as the Get an authorization code step from Step 1 except:

- The `client_id` should be that of your dynamic client
- Your token request will resemble a backend services grant, using the following parameters:
    - `client_assertion` – set to a JWT signed with your dynamic client’s private key
    - `client_assertion_type` – set to `urn:ietf:params:oauth:client-assertion-type:jwt-bearer`
    - An additional parameter per grant_type (`code` or `refresh_token`) described below

**Note that the user will once again need to log in.**

#### Exchange the authorization code for an access token

When your app makes this request to the token, it needs to authenticate itself to be issued a refresh token.  It uses
the JWT it generated to authenticate itself
per [RFC 7523 section 2.2](https://datatracker.ietf.org/doc/html/rfc7523#section-2.2). This request will look like the
JWT bearer request except the grant type is `authorization_code` instead of 
`urn:ietf:params:oauth:grant-type:jwt-bearer` and it includes the PKCE code verifier like the access token request you
made before registering the dynamic client.

In response to this request, you’ll get an access token and a refresh token.

#### Use the refresh token to get subsequent access tokens

When the access token expires, your app can generate a new JWT and use the refresh token flow to get a new access
token.  This request will look like the JWT bearer request except the grant type is `refresh_token` instead of 
`urn:ietf:params:oauth:grant-type:jwt-bearer.`

# SMART Backend Services (Backend OAuth 2.0)[](https://fhir.epic.com/Documentation?docId=oauth2&section=BackendOAuth2Guide "Copy a link to this section to your clipboard.")

**Contents**

- Building a Backend OAuth 2.0 App
- Complete Required Epic Community Member Setup to Audit Access from Your Backend Application
- Using a JWT to Obtain an Access Token for a Backend Service
    - Step 1: Creating the JWT
    - Step 2: POSTing the JWT to Token Endpoint to Obtain Access Token

## Overview

Backend apps (i.e. apps without direct end user or patient interaction) can also use OAuth 2.0 authentication through
the client_credentials OAuth 2.0 grant type. Epic's OAuth 2.0 implementation for backend services follows
the [SMART Backend Services: Authorization Guide](https://hl7.org/fhir/smart-app-launch/backend-services.html), though
it currently differs from that profile in some respects. Application vendors pre-register a public key for a given Epic
community member on the Epic on FHIR website and then use the corresponding private key to sign
a [JSON Web Token (JWT)](https://tools.ietf.org/html/rfc7519) which is presented to the authorization server to obtain
an access token.

![](https://fhir.epic.com/Content/images/OAuth2/Backend_Systems.png)

## Building a Backend OAuth 2.0 App[](https://fhir.epic.com/Documentation?docId=oauth2&section=Backend-Oauth2_Building-Backend-Apps "Copy a link to this section to your clipboard.")

To use the client_credentials OAuth 2.0 grant type to authorize your backend application's access to patient
information, two pieces of information need to be shared between the authorization server and your application:

1. **Client ID**: The client ID identifies your application to authentication servers within the Epic community and
   allows you to connect to any organization.
2. **Public key**: The public key is used to validate your signed JSON Web Token to confirm your identity.

You can register your application for access to both the sandbox and Epic
organizations [here](https://fhir.epic.com/Developer/Apps). You'll provide information, including a JSON Web Key set URL
(JWK Set URL or JKU) that hosts a public key used to verify the JWT signature. Epic will generate a client ID for you.
Your app will need to have both the  _Backend Systems_ radio button selected and the  _Use OAuth 2.0_ box checked in
order to register a JKU for backend OAuth 2.0.

### Complete Required Epic Community Member Setup to Audit Access from Your Backend Application[](https://fhir.epic.com/Documentation?docId=oauth2&section=Backend-Oauth2_Customer-Setup "Copy a link to this section to your clipboard.")

Note: This build is not needed in the sandbox. The sandbox automatically maps your client to a user, removing the need
for this setup.

When performing setup with an Epic community member, their Epic Client Systems Administrator (ECSA) will need to map
your client ID to an Epic user account that will be used for auditing web service calls made by your backend
application.  **Your app will not be able to obtain an access token until this build is completed.**

## Using a JWT to Obtain an Access Token for a Backend Service[](https://fhir.epic.com/Documentation?docId=oauth2&section=Backend-Oauth2_Getting-Access-Token "Copy a link to this section to your clipboard.")

You will generate a one-time use JSON Web Token (JWT) to authenticate your app to the authorization server and obtain an
access token that can be used to authenticate your app's web service calls. There are several libraries for creating
JWTs. See [jwt.io](https://jwt.io/) for some examples.

### Step 1: Creating the JWT[](https://fhir.epic.com/Documentation?docId=oauth2&section=Backend-Oauth2_Creating-JWT "Copy a link to this section to your clipboard.")

See the Creating a Public Private Key Pair for JWT Signature and the Creating a JWT sections for details on how to
create a signed JWT.

### Step 2: POSTing the JWT to Token Endpoint to Obtain Access Token[](https://fhir.epic.com/Documentation?docId=oauth2&section=Backend-Oauth2_Access-Token-Request "Copy a link to this section to your clipboard.")

Your application makes a HTTP POST request to the authorization server's OAuth 2.0 token endpoint to obtain access
token. The following form-urlencoded parameters are required in the POST body:

- `grant_type`: This should be set to `client_credentials`.
- `client_assertion_type`: This should be set to `urn:ietf:params:oauth:client-assertion-type:jwt-bearer`.
- `client_assertion`: This should be set to the JWT you created above.

Here is an example request:

```
POST https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token HTTP/1.1
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_assertion_type=urn%3Aietf%3Aparams%3Aoauth%3Aclient-assertion-type%3Ajwt-bearer&client_assertion=eyJhbGciOiJSUzM4NCIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJkNDUwNDljMy0zNDQxLTQwZWYtYWI0ZC1iOWNkODZhMTcyMjUiLCJzdWIiOiJkNDUwNDljMy0zNDQxLTQwZWYtYWI0ZC1iOWNkODZhMTcyMjUiLCJhdWQiOiJodHRwczovL2ZoaXIuZXBpYy5jb20vaW50ZXJjb25uZWN0LWZoaXItb2F1dGgvb2F1dGgyL3Rva2VuIiwianRpIjoiZjllYWFmYmEtMmU0OS0xMWVhLTg4ODAtNWNlMGM1YWVlNjc5IiwiZXhwIjoxNTgzNTI0NDAyLCJuYmYiOjE1ODM1MjQxMDIsImlhdCI6MTU4MzUyNDEwMn0.dztrzHo9RRwNRaB32QxYLaa9CcIMoOePRCbpqsRKgyJmBOGb9acnEZARaCzRDGQrXccAQ9-syuxz5QRMHda0S3VbqM2KX0VRh9GfqG3WJBizp11Lzvc2qiUPr9i9CqjtqiwAbkME40tioIJMC6DKvxxjuS-St5pZbSHR-kjn3ex2iwUJgPbCfv8cJmt19dHctixFR6OG-YB6lFXXpNP8XnL7g85yLOYoQcwofN0k8qK8h4uh8axTPC21fv21mCt50gx59XgKsszysZnMDt8OG_G4gjk_8JnGHwOVkJhqj5oeg_GdmBhQ4UPuxt3YvCOTW9S2vMikNUnxrhdVvn2GVg
```

And here is an example response body assuming the authorization server approves the request:

```
{
"access_token": "i82fGhXNxmidCt0OdjYttm2x0cOKU1ZbN6Y_-zBvt2kw3xn-MY3gY4lOXPee6iKPw3JncYBT1Y-kdPpBYl-lsmUlA4x5dUVC1qbjEi1OHfe_Oa-VRUAeabnMLjYgKI7b",
"token_type": "bearer",
"expires_in": 3600,
"scope": "Patient.read Patient.search"
}
```

- **Note:** See the Epic-Issued OAuth 2.0 Tokens appendix section for details on handling access tokens.

# JSON Web Tokens (JWTs)[](https://fhir.epic.com/Documentation?docId=oauth2&section=JsonWebTokens "Copy a link to this section to your clipboard.")

**Contents**

- Creating a Public Private Key Pair for JWT Signature
    - OpenSSL
    - Windows PowerShell
    - Finding the Public Key Certificate Fingerprint (Also Called Thumbprint)
- Creating a JWT
- JSON Web Key Sets
    - Key Identifier Requirements
- Hosting a JWK Set URL
    - App-Level vs Licensing Specific JWK Set URLs
    - Registering a JWK Set URL
    - Key Rotation
- Providing Multiple Public Keys
    - How
    - Why Not?
    - Use Cases

## Background

[JSON Web Tokens (JWTs)](https://tools.ietf.org/html/rfc7519) can be used for some OAuth 2.0 workflows. JWTs are
required to be used by the client credentials flow used by backend services, and can be used, as an alternative to
client secrets, for apps that use the confidential client profile or refresh tokens. You will generate a one-time use
JWT to authenticate your app to the authorization server and obtain an access token that can be used to authenticate
your app's web service calls, and potentially a refresh token that can be used to get another access token without a
user needing to authorize the request. There are several libraries for creating JWTs. See [jwt.io](https://jwt.io/) for
some examples.

## Creating a Public Private Key Pair for JWT Signature[](https://fhir.epic.com/Documentation?docId=oauth2&section=Creating-Key-Pair "Copy a link to this section to your clipboard.")

There are several tools you can use to create a key pair. As long as you can export your public key to a base64 encoded
X.509 certificate (backend clients) or JSON Web Key Set (frontend confidential clients) for registration on the Epic on
FHIR website, or you can host it on a properly formatted JWK Set URL, the tool you use to create the key pair and file
format used to store the keys is not important.  **If your app is hosted by Epic community members ("on-prem" hosting)
instead of cloud hosted, you should have unique key pairs for each Epic community member you integrate with. In
addition, you should always use unique key pairs for non-production and production systems.**

Here are examples of two tools commonly used to generate key pairs:

### Creating a Public Private Key Pair: OpenSSL[](https://fhir.epic.com/Documentation?docId=oauth2&section=Creating-Key-Pair_OpenSSL "Copy a link to this section to your clipboard.")

You can create a new private key named `privatekey.pem` using OpenSSL with the following command:

`openssl genrsa -out /path_to_key/privatekey.pem 2048`

Make sure the key length is at least 2048 bits.

For backend apps, you can export the public key to a base64 encoded X.509 certificate named `publickey509.pem` using
this command:

`openssl req -new -x509 -key /path_to_key/privatekey.pem -out /path_to_key/publickey509.pem -subj '/CN=myapp'`

Where `'/CN=myapp'` is the subject name (for example the app name) the key pair is for. The subject name does not have a
functional impact in this case but it is required for creating an X.509 certificate.

For frontend confidential clients, refer to JSON Web Key Sets.

### Creating a Public Private Key Pair: Windows PowerShell[](https://fhir.epic.com/Documentation?docId=oauth2&section=Creating-Key-Pair_Powershell "Copy a link to this section to your clipboard.")

You can create a key pair using Windows PowerShell with this command, making sure to run PowerShell as an administrator:

`New-SelfSignedCertificate -Subject "MyApp"`

_Note that the Epic on FHIR website is extracting the public key from the certificate and discarding the rest of the
certificate information, so it's not 'trusting' the self-signed certificate per se._

The subject name does not have a functional impact in this case, but it is required for creating an X.509 certificate.
Note that this is only applicable to backend apps. For frontend confidential clients, refer to JSON Web Key Sets.

You can export the public key using either PowerShell or Microsoft Management Console.

If you want to export the public key using PowerShell, take note of the certificate thumbprint and storage location
printed when you executed the `New-SelfSignedCertificate` command.

```
PS C:\dir> New-SelfSignedCertificate -Subject "MyApp"

PSParentPath: Microsoft.PowerShell.Security\Certificate::LocalMachine\MY

Thumbprint                                Subject
----------                                -------
3C4A558635D67F631E5E4BFDF28AE59B2E4421BA  CN=MyApp
```

Then export the public key to a X.509 certificate using the following commands (using the thumbprint and certificate
location from the above example):

`PS C:\dir> $cert = Get-ChildItem -Path Cert:\LocalMachine\My\3C4A558635D67F631E5E4BFDF28AE59B2E4421BA`

`PS C:\dir> Export-Certificate -Cert $cert -FilePath newpubkeypowerShell.cer`

Export the binary certificate to a base64 encoded certificate:

`PS C:\dir> certutil.exe -encode newpubkeypowerShell.cer newpubkeybase64.cer`

If you want to export the public key using Microsoft Management Console, follow these steps:

1. Run (Windows + R) > mmc (Microsoft Management Console).
2. Go to File > Add/Remove SnapIn.
3. Choose Certificates > Computer Account > Local Computer. The exact location here depends on the certificate store
   that was used to create and store the keys when you ran the `New-SelfSignedCertificate` PowerShell command above. The
   store used is displayed in the printout after the command completes.
4. Then back in the main program screen, go to Personal > Certificates and find the key pair you just created in step 1.
   The "Issue To" column will equal the value passed to `-Subject` during key creation (e.g. "MyApp" above).
5. Right click on the certificate > All Tasks > Export.
6. Choose option to not export the private key.
7. Choose to export in base-64 encoded X.509 (.CER) format.
8. Choose a file location.

### Finding the Public Key Certificate Fingerprint (Also Called Thumbprint)[](https://fhir.epic.com/Documentation?docId=oauth2&section=Finding-Key-Fingerprint "Copy a link to this section to your clipboard.")

The [public key certificate fingerprint](https://en.wikipedia.org/wiki/Public_key_fingerprint)  (also known as
thumbprint in Windows software) is displayed for JWT signing public key certificates that are uploaded to the Epic on
FHIR website. There are a few ways you can find the fingerprint for a public key certificate:

- If you created the public key certificate using Windows PowerShell using the steps above, the thumbprint was displayed
  after completing the command. You can also print public keys and their thumbprints for a given certificate storage
  location using the `Get-ChildItem` PowerShell command.

  For example, run `Get-ChildItem -Path Cert:\LocalMachine\My` to find all certificate thumbprints in the local machine
  storage.

- You can follow
  the [steps here](https://docs.microsoft.com/en-us/dotnet/framework/wcf/feature-details/how-to-retrieve-the-thumbprint-of-a-certificate)
   to find the thumbprint of a certificate in Microsoft Management Console.
- You can run this OpenSSL command to print the public key certificate fingerprint that would be displayed on the Epic
  on FHIR website, replacing openssl_publickey.cert with the name of your public key certificate:

  `$ openssl x509 -noout -fingerprint -sha1 -inform pem -in openssl_publickey.cert`

  Note that the output from this command includes colons between bytes which are not shown on the Epic on FHIR website.

## Creating a JWT[](https://fhir.epic.com/Documentation?docId=oauth2&section=Creating-JWTs "Copy a link to this section to your clipboard.")

The JWT should have these headers:

| Header | Description                                                                                                                                                                                                                                                                                                                                                                                                     |
|--------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `alg`  | The JWT authentication algorithm. Epic supports the following RSA signing algorithms for all confidential clients: RS256, RS384, and RS512. For clients that register a JSON Web Key Set URL, Epic also supports the following Elliptic Curve algorithms: ES256, ES384. Epic does not support manual upload of Elliptic Curve public keys, and we recommend new apps use a JWK Set URL and Elliptic Curve keys. |
| `typ`  | If provided, this should always be set to `JWT`.                                                                                                                                                                                                                                                                                                                                                                |
| `kid`  | For apps using JSON Web Key Sets (including dynamically registered clients), we recommend setting this value to the `kid` of the target public key from your key set.                                                                                                                                                                                                                                           |
| `jku`  | For apps using JSON Web Key Set URLs, optionally set this value to the URL you registered on your application                                                                                                                                                                                                                                                                                                   |

The JWT header should be formatted as follows:

```
{
"alg": "RS384",
"typ": "JWT"
}
```

The JWT should have these claims in the payload:

| Claim | Description                                                                                                                                                                | Remarks                                                                                                                                                                                                                                                                                                                                                                                |
|-------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `iss` | Issuer of the JWT. This is the app's `client_id`, as determined during registration on the Epic on FHIR website, or the `client_id` returned during a dynamic registration | This is the same as the value for the `sub` claim.                                                                                                                                                                                                                                                                                                                                     |
| `sub` | Issuer of the JWT. This is the app's `client_id`, as determined during registration on the Epic on FHIR website, or the `client_id` returned during a dynamic registration | This is the same as the value for the `iss` claim.                                                                                                                                                                                                                                                                                                                                     |
| `aud` | The FHIR authorization server's token endpoint URL. This is the same URL to which this authentication JWT will be posted. See below for an example POST.                   | It's possible that Epic community member systems will route web service traffic through a proxy server, in which case the URL the JWT is posted to is not known to the authorization server, and the JWT will be rejected. For such cases, Epic community member administrators can add additional audience URLs to the allowlist, in addition to the FHIR server token URL if needed. |
| `jti` | A unique identifier for the JWT.                                                                                                                                           | The `jti` must be no longer than 151 characters and cannot be reused during the JWT's validity period, i.e., before the `exp` time is reached. In the November 2024 version of Epic and later, this value must be a string.                                                                                                                                                            |
| `exp` | Expiration time integer for this authentication JWT, expressed in seconds since the "Epoch" (1970-01-01T00:00:00Z UTC).                                                    | The `exp` value must be in the future and can be no more than 5 minutes in the future at the time the access token request is received.                                                                                                                                                                                                                                                |
| `nbf` | Time integer before which the JWT must not be accepted for processing, expressed in seconds since the "Epoch" (1970-01-01T00:00:00Z UTC).                                  | The `nbf` value cannot be in the future, cannot be more recent than the `exp` value, and the `exp` - `nbf` difference cannot be greater than 5 minutes.                                                                                                                                                                                                                                |
| `iat` | Time integer for when the JWT was created, expressed in seconds since the "Epoch" (1970-01-01T00:00:00Z UTC).                                                              | The `iat` value cannot be in the future, and the `exp` - `iat` difference cannot be greater than 5 minutes.                                                                                                                                                                                                                                                                            |

Here's an example JWT payload:

```
{
"iss": "d45049c3-3441-40ef-ab4d-b9cd86a17225",
"sub": "d45049c3-3441-40ef-ab4d-b9cd86a17225",
"aud": "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token",
"jti": "f9eaafba-2e49-11ea-8880-5ce0c5aee679",
"exp": 1583524402,
"nbf": 1583524102,
"iat": 1583524102
}
```

The header and payload are then base64 URL encoded, combined with a period separating them, and cryptographically signed
using the private key to generate a signature. Conceptually:

```
signature = RSA-SHA384(base64urlEncoding(header) + '.' + base64urlEncoding(payload), privatekey)
```

The full JWT is constructed by combining the header, body and signature as follows:

```
base64urlEncoding(header) + '.' + base64urlEncoding(payload) + '.' + base64urlEncoding(signature)
```

A fully constructed JWT looks something like this:

```
eyJhbGciOiJSUzM4NCJ9.eyJpYXQiOjE3MDc0MjQzMzYsImlzcyI6IjAxZjBlMzllLTIyY2MtNGFiNS05NTE0LWMyN2Y5Mzg0NzFjOCIsInN1YiI6IjAxZjBlMzllLTIyY2MtNGFiNS05NTE0LWMyN2Y5Mzg0NzFjOCIsImF1ZCI6Imh0dHBzOi8vdmVuZG9yc2VydmljZXMuZXBpYy5jb20vaW50ZXJjb25uZWN0LWFtY3VycHJkLW9hdXRoL29hdXRoMi90b2tlbiIsImV4cCI6MTcwNzQyNDYzNiwianRpIjoiMGIyZjZmMTQtMThkZS01NTU1LWJkMjUtZTNkMzg0ZmEwZTVkIn0.hE8DAq72XjJWNci5JFEuN5TrLh9W1UZP4H904GTIhggZNnNxtJLvUPKPrpf9PYgMZmgHb6peVR5MMUnQRtFHLZsH_C2v9ridfIA8BgWBL5eNiXcriUM9rIEvHwImgbKyPQUY_a0D7BCXiLqdjMAdEZe_afxCQBW0I7EsGqbpA0agayhLIDrlhcfCtdBrGyBlwW2zlUjTUo8Rj2o59tL90w6mVykeZIsJfcLQF4jeSfNOko-908mha_Gga6szWCqxlCr5ed9i6mjRSUQByG-GAFnCIhPULeedCHEgb9WF2NC0cImjK92jGSM6i43Y19uHvCFYMEkBMDaDK7WQigm3yg
```

## JSON Web Key Sets[](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS "Copy a link to this section to your clipboard.")

Applications using JSON Web Token (JWT) authentication can provide their public keys to Epic as
a [JSON Web Key (JWK) Set](https://datatracker.ietf.org/doc/html/rfc7517). Epic accepts keys of type RSA or EC. EC is
only support for JWKS returned from a JWKS URL and is not supported for static JWKS uploaded directly. When using a JWKS
URL we recommend including the kid values in your keys and in the headers of your assertions. Note that when uploading a
static JWKS directly, only the "kty", "e" and "n" properties will be saved from the uploaded keys. Other fields
typically present in a JWKS like "kid" or "alg" can exist in the uploaded JWKS without an error but they will be
discarded upon save.

In the example key set below, the "RSA" key can be used with the RS256, RS384 or RS512 signing algorithms. The "EC" key
can only be used with the "ES384" algorithm because it specifies a curve of `P-384`. The algorithm is determined when
you first generate the key, like in the example above.

        {
            "keys": [
                { "kty":"RSA", "e": "AQAB", "kid": "d1fc1715-3299-43ec-b5de-f943803314c2", "n": "uPkpNCkqbbismKNwKguhL0P4Q40sbyUkUFcmDAACqBntWerfjv9VzC3cAQjwh3NpJyRKf7JvwxrbELvPRMRsXefuEpafHfNAwj3acTE8xDRSXcwzQwd7YIHmyXzwHDfmOSYW7baAJt-g_FiqCV0809M9ePkTwNvjpb6tlJu6AvrNHq8rVn1cwvZLIG6KLCTY-EHxNzsBblJYrZ5YgR9sfBDo7R-YjE8c761PSrBmUM4CAQHtQu_w2qa7QVaowFwcOkeqlSxZcqqj8evsmRfqJWoCgAAYeRIsgKClZaY5KC1sYHIlLs2cp2QXgi7rb5yLUVBwpSWM4AWJ_J5ziGZBSQYB4sWWn8bjc5-k1JpUnf88-UVZv9vrrkMJjNam32Z6FNm4g49gCVu_TH5M83_pkrsNWwCu1JquY9Z-eVNCsU_AWPgHeVZyXT6giHXZv_ogMWSh-3opMt9dzPwYseG9gTPXqDeKRNWFEm46X1zpcjh-sD-8WcAlgaEES6ys_O8Z" },
                { "kty":"EC", "kid": "iTqXXI0zbAnJCKDaobfhkM1f-6rMSpTfyZMRp_2tKI8", "crv": "P-384", "x": "C1uWSXj2czCDwMTLWV5BFmwxdM6PX9p-Pk9Yf9rIf374m5XP1U8q79dBhLSIuaoj", "y": "svOT39UUcPJROSD1FqYLued0rXiooIii1D3jaW6pmGVJFhodzC31cy5sfOYotrzF" }
            ]
        }	

### Key Identifier Requirements[](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-kids "Copy a link to this section to your clipboard.")

Epic recommends your application provide the `kid` field in JWK Sets and in your JWT Headers when using a JWKS URL,
since Epic can use the identifier to find your intended key. Epic does not require your key identifiers to
be [thumbprints](https://datatracker.ietf.org/doc/html/rfc7638), and will accept any value that is unique within your
key set.

## Hosting a JWK Set URL[](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-URLS "Copy a link to this section to your clipboard.")

Starting in the February 2024 version of Epic, customers can require back-end apps using JWT authentication to use
a JSON Web Key Set URL to provide public keys to Epic for JWT verification. All customers will require this when they
upgrade to the May 2026 version of Epic. JWK Set URLs streamline implementation and make key rotation feasible by
providing a centralized and trusted place where Epic community members can retrieve your public keys.

**Implementation Consideration:** Starting in the May 2026 version of Epic, customers must manually configure your
specified JKU for back-end OAuth 2.0 clients in their system. This is referred to as the local JKU.

### App-Level vs Licensing Specific JWK Set URLs[](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-URL-App-Vs-License "Copy a link to this section to your clipboard.")

You can provide an app-level JWK Set URL when building your app for use in the sandbox environments. For security
reasons, we require you at least have different URLs for non-production and production. We also  _recommend_ that you
use a different set of JWK Set URLs for each Epic community member, and that key sets are not shared between different
JWK Set URLs. However, this is not required, and reusing JWK URLs across customers may be appropriate for cloud-based
apps that can re-use a single private key (without making a copy).

However, if your application is hosted by Epic community members ("on-prem", applications), then you must provide
different JWK Set URLs for every licensed community member. There are 2 reasons on-prem applications should use distinct
URLs:

1. These applications require physically separate servers, and cannot share a private key across installations without
   copying and transporting the key (a security risk)
2. Attempting to aggregate multiple public keys behind one URL is a security risk if those keys are in physically
   different places. If any one of your server environments is compromised, it can be used to impersonate any other
   instance of your application

### Non-Production and Production Keys[](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-URL-Non-Prd-Prd "Copy a link to this section to your clipboard.")

Vendor Services and Epic on FHIR will not allow non-production JWK Set URLs to be registered as production JWK Set URLs
for new apps and new app versions.

Your application should use separate key pairs for non-production and production environments. If both JWK Set URLs host
the same public key, the corresponding private key can authenticate your application to either environment. This means a
compromise of your non-production key material, where access controls are typically less rigorous, could be used to
obtain production access tokens with your app's authorized scopes.

Private keys should not be stored in plaintext configuration files, environment variables, or source code repositories
in any environment. Use your hosting provider's key management service (such as AWS KMS, Azure Key Vault, GCP Cloud KMS,
etc.) or a hardware security module where available.

### Registering a JWK Set URL[](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-URL-Registration "Copy a link to this section to your clipboard.")

When building or licensing an application that will authenticate against Epic, you are prompted to optionally upload
non-production and production JWK Set URLs to your app. When registering these URLs, be sure of the following:

1. Are secured with TLS
2. Are publicly accessible
3. Do not require authentication
4. Will not change over time
5. Are responsive

Epic will verify points 1-3 whenever you provide a JWK Set URL

There are separate non-production and production URLs because we recommend you use separate key sets for each.

Note that in addition to registering different URLs, you should ensure the public keys hosted at each URL are themselves
different. Distinct URLs that serve the same underlying key set still allow the same private key to authenticate to both
production and non-production environments, which leaves production vulnerable if the non-production private key is
compromised.

### Key Rotation[](https://fhir.epic.com/Documentation?docId=oauth2&section=JWKS-URL-Rotation "Copy a link to this section to your clipboard.")

Before rotating your keys, remove any static public keys from your existing downloads

One benefit of JWK Set URLs is that your application can rotate its key as needed, and Epic can dynamically fetch the
updated keys. It is a best practice to periodically rotate your private keys, and if you choose to do so, we recommend
the following strategy:

1. Create your new public-private key pair ahead of time
2. Add your new public key to your JWK Set in addition to the existing one
3. Start using your new private key for signing JWTs
4. Once you are certain your system is no longer using the old private key, wait 5 minutes (the maximum JWT expiration
   time) remove the old public key from your JWK Set

In between steps 3 and 4, Epic will notice you are using a new private key and will automatically fetch the new key set.
Every time you present a JWT to Epic we will check your JWK Set URL at most once, and only if the cached keys are
expired or did not verify your JWT

Epic's default and maximum cache time is 29 hours, but your endpoint can override the default by using
the [Cache-Control](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cache-Control) HTTP response header. This
is most useful during development and testing when you may want to update your non-production public keys ad-hoc, or to
test the availability of your server from Epic. You could for instance respond with "Cache-Control": "no-store" to stop
Epic from storing your keys at all. This is not recommended for production usage since you lose the performance benefits
of caching.

Using separate key pairs per environment allows you to rotate non-production and production keys on independent
schedules whereas shared key pairs make independent rotation not possible.

## Providing Multiple Public Keys[](https://fhir.epic.com/Documentation?docId=oauth2&section=Multi-Public-Keys "Copy a link to this section to your clipboard.")

### How[](https://fhir.epic.com/Documentation?docId=oauth2&section=Mulit-Keys-How "Copy a link to this section to your clipboard.")

Apps can provide multiple public keys to an Epic community member by providing a JWK Set URL that contains multiple
keys.

**Do not host more than 100 public keys on a single JKU. Epic does not support this.**

### Why Not?[](https://fhir.epic.com/Documentation?docId=oauth2&section=Multi-Keys-Why-Not "Copy a link to this section to your clipboard.")

In general, the only reason to provide multiple public keys on one JWK Set URL is if your system spans multiple separate
servers or cloud environments, and so you cannot share a private key between them. If you can re-use a key without
making copies of it, then you should re-use that key and focus on protecting it with the best policy available (For
example, using your hosting provider's key store or using
a [Trusted Platform Module](https://en.wikipedia.org/wiki/Trusted_Platform_Module)).

### Use Cases[](https://fhir.epic.com/Documentation?docId=oauth2&section=Multi-Keys-Use-Cases "Copy a link to this section to your clipboard.")

Example use cases:

- Having multiple isolated testing environments, where you may provide multiple non-production public keys (one for each
  environment)
- On-premises applications with multiple servers hosted by a given community member. In this case you would provide
  multiple production public keys (one for each server)

# Appendix[](https://fhir.epic.com/Documentation?docId=oauth2&section=Appendix "Copy a link to this section to your clipboard.")

## Epic-Issued OAuth 2.0 Tokens

Starting in the May 2020 version of Epic, Epic community members can enable a feature that makes all OAuth 2.0 tokens
and codes into [JSON Web Tokens (JWTs)](https://jwt.io/) instead of opaque tokens. This feature increases the length of
these tokens significantly and is enabled in the sandbox. Developers should ensure that app URL handling does not
truncate OAuth 2.0 tokens and codes.

The format of these tokens is helpful for troubleshooting, but Epic strongly recommends against relying on these tokens
being JWTs. This same feature can be turned off by Epic community members, and Epic may need to change the format of
these tokens in the future.

In general, if the "aud" parameter of a JWT is not your app's client ID, you should not be decoding it programmatically.
For the 5 types of JWTs mentioned in this tutorial, here's a breakdown of their audience (aud) and issuers (iss):

| JWT Scenario            | Audience (aud)       | Issuer (iss)         |
|-------------------------|----------------------|----------------------|
| Launch Token            | Authorization server | Authorization server |
| Authorization Code      | Authorization server | Authorization server |
| Access Token            | Authorization server | Authorization server |
| Openid Connect ID Token | The app's client_id  | Authorization server |
| Client Assertion        | Authorization server | The app's client_id  |

### Trusting an OAuth 2.0 Token

A common question that developers ask is how an app can "trust" an OAuth 2.0 token without cryptographically verifying
it. Our answer is that your app should never trust a token by itself and should rely on the authorization server to
verify a given token. When redeeming an authorization code for an access token, your app can trust that if the request
succeeded that the authorization code was valid, and you can then create a session in your application. The only
exception is an openid connect id_token, which is designed to be verified by the client application.

## Epic-Issued OAuth 2.0 Tokens

Starting in the May 2020 Epic version, Epic community members can enable a feature that makes all OAuth 2.0 tokens and
codes into [JSON Web Tokens (JWTs)](https://jwt.io/) instead of opaque tokens. This feature increases the length of
these tokens significantly and is enabled in the vendorservices.epic.com sandbox. Developers should ensure that app URL
handling does not truncate OAuth 2.0 tokens and codes.

The format of these tokens is helpful for troubleshooting, but Epic strongly recommends against relying on these tokens
being JWTs. This same feature can be turned off by Epic community members, and Epic may need to change the format of
these tokens in the future.

In general, if the "aud" parameter of a JWT is not your app's client ID, you should not be decoding it programmatically.
For the 5 types of JWTs mentioned in this tutorial, here's a breakdown of their audience (aud) and issuers (iss):

| JWT Scenario            | Audience (aud)       | Issuer (iss)         |
|-------------------------|----------------------|----------------------|
| Launch Token            | Authorization server | Authorization server |
| Authorization Code      | Authorization server | Authorization server |
| Access Token            | Authorization server | Authorization server |
| Openid Connect ID Token | The app's client_id  | Authorization server |
| Client Assertion        | Authorization server | The app's client_id  |

### Trusting an OAuth 2.0 Token

A common question that developers ask is how an app can "trust" an OAuth 2.0 token without cryptographically verifying
it. Our answer is that your app should never trust a token by itself and should rely on the authorization server to
verify a given token. When redeeming an authorization code for an access token, your app can trust that if the request
succeeded that the authorization code was valid, and you can then create a session in your application. The only
exception is an openid connect id_token, which is designed to be verified by the client application.

How you handle the authorization code is the most important step in an OAuth 2.0 flow. See our best practice for using
this step to secure your application.

## Refresh Tokens and Persistent Access Periods[](https://fhir.epic.com/Documentation?docId=oauth2&section=Refresh-Tokens-and-Persistent-Access-Periods "Copy a link to this section to your clipboard.")

The time during which a client can use refresh tokens to get new access is known as the persistent access period. In
patient-facing contexts, it can be set by the patient through MyChart. In non-patient-facing contexts, the duration will
be set by the refresh token itself. Specifically, the start of the period is set to the time the token is provisioned
and the end of the period is set to the token's expiration date-time.

In many situations, only one access token will want to be requested with the original refresh token and the persistent
access period just defines when the that refresh token can do so. However, a client will often need access over a longer
period of time. In these situations, a single refresh token won't be an efficient solution as a new authorization
request will have to be made after the token is used or the period ends. There are two workflows for getting an extended
persistent access period by utilizing multiple refresh tokens: rolling refresh tokens and indefinite access.

### Rolling Refresh Persistent Access Periods[](https://fhir.epic.com/Documentation?docId=oauth2&section=Rolling-Refresh-Persistent-Access-Periods "Copy a link to this section to your clipboard.")

In this workflow, multiple refresh tokens are used to have the persistent access period last as long as a single refresh
token could. A first refresh token is provisioned and the persistent access period is set as normal (i.e., start of
period = time of provisioning and end of period = token expiration date-time). This first refresh token can then be used
in another access token request that specifies to return a refresh token. The refresh token granted from this access
token request will be generated normally except for the fact that its expiration date-time will be set to the same as
the first refresh token. All subsequent refresh tokens in the workflow will also have this same expiration date-time.

This means that the persistent access period of a rolling refresh workflow is defined by the first refresh token and all
subsequent refresh tokens 'fall within' that period. The below diagram gives a visual representation of the persistent
access period during a rolling refresh token workflow.

For patient-facing apps, the length of the persistent access period for a rolling refresh will be set by each patient
during the OAuth 2.0 flow. They can choose from a variety of options (e.g., 1 hours, 1 day, 1 week, …) configured by the
customer.

To enable rolling refresh access for non-patient-facing apps, check that your customers have properly configured their
external client record.

![](https://fhir.epic.com/Content/images/OAuth2/RollingRefresh.png)

### Indefinite Persistent Access Periods [](https://fhir.epic.com/Documentation?docId=oauth2&section=Indefinite-Persistent-Access-Periods "Copy a link to this section to your clipboard.")

In this workflow, the persistent access period lasts indefinitely. It does so by using consecutive refresh tokens like
in rolling refresh workflows. The notable difference is that, here, new refresh tokens have an expiration date-time
greater than the one before it. In the rolling refresh workflow, this is always set to that of the first token. Since
the persistent access period end date-time is set by the token's expiration, continually setting new refresh tokens to
expire in the future will make the persistent access period last until no more refresh tokens are requested.

To enable indefinite access for patient-facing apps, instruct your customers to follow these configuration steps. 
**Note that this only gives patients the option of indefinite access. Each patient still chooses their max access
period.**

1. Navigate to Login and Access Configuration in MyChart.
2. Navigate to OAuth Access Duration Configuration.
3. Specify 'Indefinite' in the Global Max OAuth Access Duration field.

To enable indefinite access for non-patient-facing apps, check that your customers have properly configured their
external client records.

![](https://fhir.epic.com/Content/images/OAuth2/IndefiniteAccess.png)

## Limit Use of Localhost URIs[](https://fhir.epic.com/Documentation?docId=oauth2&section=Limit-Localhost "Copy a link to this section to your clipboard.")

Launched applications must register a list of URLs that Epic will use as an allowlist during OAuth 2.0. Epic will only
deliver authorization codes to a URL that is listed on the corresponding app.

As a security best practice, apps should not include localhost/loopback URIs in this list. An exception is for Native
applications which are designed to use localhost for the redirect, such as certain libraries available from
the [AppAuth group](https://appauth.io/). An alternative available for native apps are platform specific link
associations, discussed in our Considerations for Native Apps below.

If your developers wish to use localhost URIs for testing in our sandboxes, we recommend creating a second test
application that will never be activated in customer environments and listing your localhost URLs there.

## Considerations for Native Apps[](https://fhir.epic.com/Documentation?docId=oauth2&section=Native-Apps-Consider "Copy a link to this section to your clipboard.")

Additional security considerations must be taken into account when launching a native app to sufficiently protect the
authorization code from malicious apps running on the same device. There are a few options for how to implement this
additional layer of security:

- Platform-specific link association: Android, iOS, Windows, and other platforms have introduced a method by which a
  native app can claim an HTTPS redirect URL, demonstrate ownership of that URL, and instruct the OS to invoke the app
  when that specific HTTPS URL is navigated to. Apple's iOS calls this feature
  "[Universal Links](https://developer.apple.com/library/archive/documentation/General/Conceptual/AppSearch/UniversalLinks.html)
  "; Android, "[App Links](https://developer.android.com/training/app-links/)"; Windows,
  "[App URI Handlers](https://docs.microsoft.com/en-us/windows/uwp/launch-resume/web-to-app-linking)". This association
  of an HTTPS URL to native app is platform-specific and only available on some platforms.
- Proof Key for Code Exchange (PKCE): This is a standardized, cross-platform technique for public clients to mitigate
  the threat of authorization code interception. However, it requires additional support from the authorization server
  and app. PKCE is described in [IETF RFC 7636](https://tools.ietf.org/html/rfc7636) and supported starting in the
  August 2019 version of Epic. Note that the Epic implementation of this standard uses the S256 code_challenge_method.
  The "plain" method is not supported.

Starting in the August 2019 version of Epic, the Epic implementation of OAuth 2.0 supports PKCE and Epic recommends
using PKCE for native mobile app integrations. For earlier versions or in cases where PKCE cannot be used, Epic
recommends the use of Universal Links/App Links in lieu of custom redirect URL protocol schemes.

## Subspace Security Considerations[](https://fhir.epic.com/Documentation?docId=oauth2&section=desktop-app-authentication "Copy a link to this section to your clipboard.")

This section applies to certain applications with a user type of "Backend Systems". If your application does not fit
this criterion, you can skip this section. You can also skip this section if your Backend application does not use
Subspace APIs; in which case you should remove the "Subspace" functionality and un-check the "Can Register Dynamic
Clients" checkbox.

Beginning March 2, 2023, Epic no longer supports registering new client IDs with the following combination of settings:

1. User type of Backend Systems

2. Uses OAuth 2.0

3. Functionality: Subspace

4. Registers Dynamic Clients

5. Functionality: Incoming Web Services

The combination of settings 1-4 has historically been used for integration with Epic's Subspace Communication Framework.
In this approach your application registers workstation-specific private keys to authorize Subspace API calls.

As a security best practice, for integrations using the above settings, instead it is recommended to authenticate the
Incoming Web Services (Setting 5) using the OAuth 2.0 token obtained from a SMART on FHIR app launch or configuring your
integration to use a multi-context application.

When a workstation-specific private key is allowed to authorize web service calls, a user may be able to bypass
user-specific authentication and authorization checks within the web services themselves. This risk is mitigated in
virtualized environments where the workstation (and the private key) is controlled by administrators. These security
implications do not apply to Subspace APIs as Subspace APIs provide access to the Epic database only after the user has
authenticated against Epic.

As a security best practice, rather than using the combination of settings above, an application that needs all these
features (particularly web services) can do one of the following:

### SMART App Launch[](https://fhir.epic.com/Documentation?docId=oauth2&section=desktop-app-authn-launch "Copy a link to this section to your clipboard.")

We recommend following the [FHIRcast specification's](https://fhircast.org/) guidance, which says to use the SMART App
Launch to secure communication. One reason to use the SMART App Launch for Subspace integrations is that the launch
provides user-specific security. The token provided by the launch is designed for end-users to use directly and provides
the flexibility for you to call web services from any portion of your infrastructure.

Per the FHIRcast specification, the hub needs to launch your application before you can use Subspace APIs. Practically,
this means your end users need to launch your application from Epic to begin their workflow. Epic's "User Toolbar"
launch approach provides the ability for the user to launch your application before opening any patient and provides
your application with authorization to view multiple patients' charts without re-launching. This requires coordination
with the Epic Community Member to show the launch button to end users and requires instructing end users to start their
workflow in this way.

By default, launched Subspace applications will receive 1 hour of API authorization per launch. If your application
requires longer workflows, we offer the following options:

1. Epic offers a Dynamic Client Registration (DCR) workflow where you can register a temporary and in-memory private key
   to extend your application's access. Epic will revoke the key's access after a period of inactivity, so you should
   request a new access token before the most recent one expires. After the end user closes your application, you should
   delete the private key and Epic will revoke its access due to inactivity.

   Note this workflow differs from the "Backend System" DCR workflow above, in that tokens issued to the client are tied
   to the user who launched the application. In this way, Epic can conduct user-specific security checks when your
   application calls web services.

2. (Not recommended) Epic Community Members can extend the default duration of your access tokens to any value, for
   example 4 hours.

### Multi-Context Application[](https://fhir.epic.com/Documentation?docId=oauth2&section=desktop-app-authn-multi "Copy a link to this section to your clipboard.")

In this approach, your application handles the burden of authenticating the end-user and mediating their access to the
Epic database. This can only be done securely by using server-side controls; safeguards put in place in a
client-application (example: a native desktop app) are not sufficient, as they could potentially be bypassed by an end
user.

An existing "Backend System" Subspace integration can achieve this with the following:

1. Created a new client ID with the following settings:

    1. User type of Backend Systems

    2. Uses OAuth 2.0

    3. Feature: Incoming API

    4. Select your required Web Services

2. Conduct Backend OAuth 2.0 as you did with your existing app, but skip the DCR step

3. Follow our security best practices for Backend applications, which solve the user-authentication issues above

### SMART Scopes[](https://fhir.epic.com/Documentation?docId=oauth2&section=smart-scopes "Copy a link to this section to your clipboard.")

[SMART scopes](https://hl7.org/fhir/smart-app-launch/1.0.0/scopes-and-launch-context/index.html) are returned in the
scope parameter of the OAuth 2.0 token endpoint response and determine which resources an application has permission to
access and what actions the application is permitted to take. The scopes provided are based on the scopes that your app
requested in your authorize request as well as the incoming APIs you have selected on your app page.

As of the August 2024 version of Epic, both SMART v1 and SMART v2 scope formatting are supported. Prior to the August
2024 version, only SMART v1 scopes are supported.

The actions defined by SMART v1 scopes are .read and .write. SMART v2 scopes use CRUDS syntax and actions include: .c –
create, .r – read, .u – update, .d – delete, and .s – search.

For example, the Observation.Create resource corresponds to either:

- **SMART v1:** {user-type}/Observation.write
- **SMART v2:** {user-type}/Observation.c

#### Which version should you select?

If your app uses SMART v1 scopes or if your app does not use SMART scopes at all, you should select SMART v1.

If your app uses SMART v2 scopes, you should select SMART v2.

_Note: Prior to the August 2024 version of Epic, SMART v1 scope formatting will be returned regardless of the SMART
Scope Version selected on your app page. If your app uses SMART scopes and your customer is on a version of Epic earlier
than August 2024, you must support SMART v1 scopes._

## Non-OAuth 2.0 Authentication[](https://fhir.epic.com/Documentation?docId=oauth2&section=NonOauth "Copy a link to this section to your clipboard.")

Epic supports forms of authentication in addition to OAuth 2.0.  **Only use these forms of authentication if OAuth 2.0
is not an option.**

### HTTP Basic Authentication [](https://fhir.epic.com/Documentation?docId=oauth2&section=Basic-Auth "Copy a link to this section to your clipboard.")

HTTP Basic Authentication requires that Epic community members provision a username and password that your application
will provide to the web server to authenticate every request.

Epic supports HTTP Basic Authentication via a system-level user record created within Epic's database. You may hear this
referred to as an  _EMP_ record. You will need to work with an organization's Epic user security team to have a username
and password provided to you.

When calling Epic's web services with HTTP Basic Authentication the username must be provided as follows: 
`emp$<username>`. Replace <username> with the username provided by the organization during implementation of your
application.

Base64 encode your application's credentials and pass them in the HTTP Authorization header. For example, if you've been
given a system-level username/password combination that is
username/Pa$$w0rd1, the Authorization header would have a value of ZW1wJHVzZXJuYW1lOlBhJCR3MHJkMQ== (the base64 encoded version of emp$username:Pa$$
w0rd1):

```
GET http://localhost:8888/website HTTP/1.1
Host: localhost:8888
Proxy-Connection: keep-alive
Authorization: Basic ZW1wJHVzZXJuYW1lOlBhJCR3MHJkMQ==
```

#### Storing HTTP Basic Authentication Values

The username and password you use to authenticate your web service requests are extremely sensitive since they can be
used to pull PHI out of an Epic organization's system. The credentials should always be encrypted and should not be
stored directly within your client's code. You should make sure that access to decrypt the credentials should be limited
to only the users that need access to it. For example, if a service account submits the web service requests, only that
service account should be able to decrypt the credentials.

### Client Certificates and SAML tokens

Epic also supports the use of Client Certificates and SAML tokens as an authentication mechanism for server-to-server
web service requests. We do not prefer these methods because both require web server administrators to maintain a
trusted certificate for authentication. As certificates expire or servers need to be moved around, this puts an
additional burden on system administrators.

### Additional Required Headers[](https://fhir.epic.com/Documentation?docId=oauth2&section=NonOauth_Additional-Required-Headers "Copy a link to this section to your clipboard.")

When you use OAuth 2.0 authentication, Epic can automatically gather important information about the client making a web
service request. When you use a non-OAuth 2.0 authentication mechanism, we require that this additional information be
passed in an HTTP header.

#### Epic-Client-ID[](https://fhir.epic.com/Documentation?docId=oauth2&section=NonOauth_Epic-Client-ID-Header "Copy a link to this section to your clipboard.")

This is the client ID you were given upon your app's creation on the Epic on FHIR site.  **This is always required when
calling Epic's web services if your app doesn't use OAuth 2.0 authentication.**

```
GET http://localhost:8888/website HTTP/1.1
Host: localhost:8888
Proxy-Connection: keep-alive
Authorization: Basic ZW1wJHVzZXJuYW1lOlBhJCR3MHJkMQ==
Epic-Client-ID: 0000-0000-0000-0000-0000
```

#### Epic-User-ID and Epic-User-IDType[](https://fhir.epic.com/Documentation?docId=oauth2&section=NonOauth_Epic-User-Headers "Copy a link to this section to your clipboard.")

This is required for auditing requests to FHIR resources. The Epic-User-ID corresponds to an EMP (Epic User) account
that an organization creates for your application. This might be required depending on the web services you are calling.
The Epic-User-IDType is almost always  _EXTERNAL_.

```
GET http://localhost:8888/website HTTP/1.1
Host: localhost:8888
Proxy-Connection: keep-alive
Authorization: Basic ZW1wJHVzZXJuYW1lOlBhJCR3MHJkMQ==
Epic-Client-ID: 0000-0000-0000-0000-0000					 	
Epic-User-ID: username					 	
Epic-User-IDType: EXTERNAL
```
