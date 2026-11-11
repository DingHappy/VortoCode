// Desktop staff approval: a non-exportable P-256 key, gated by fresh biometry.
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <LocalAuthentication/LocalAuthentication.h>
#include <stdlib.h>
#include <string.h>

static NSString *hex(NSData *data) {
    const unsigned char *bytes = data.bytes;
    NSMutableString *result = [NSMutableString stringWithCapacity:data.length * 2];
    for (NSUInteger i = 0; i < data.length; i++) [result appendFormat:@"%02x", bytes[i]];
    return result;
}

static char *response(NSDictionary *value) {
    NSData *json = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    return strdup([[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding].UTF8String);
}

char *vorto_staff_approval(const char *tagValue, const char *messageValue) {
    @autoreleasepool {
        if (!tagValue || !messageValue || strlen(tagValue) > 256 || strlen(messageValue) > 1024)
            return response(@{@"error": @"批准请求无效"});
        NSString *tag = [NSString stringWithUTF8String:tagValue];
        NSString *message = [NSString stringWithUTF8String:messageValue];
        if (!tag || !message) return response(@{@"error": @"批准请求无效"});
        LAContext *context = [LAContext new];
        context.touchIDAuthenticationAllowableReuseDuration = 0;
        context.localizedReason = [message hasPrefix:@"vortoone-staff-action-v1\n"]
            ? @"批准专员的这一步操作，请核对 Desktop 上的动作与完整内容"
            : @"批准专员登记，请核对 Desktop 上的完整权限清单";
        NSError *availability = nil;
        if (![context canEvaluatePolicy:LAPolicyDeviceOwnerAuthenticationWithBiometrics error:&availability])
            return response(@{@"error": @"此 Mac 没有可用的 Touch ID，请求仍保持待批"});
        NSData *applicationTag = [tag dataUsingEncoding:NSUTF8StringEncoding];
        NSDictionary *query = @{
            (__bridge id)kSecClass: (__bridge id)kSecClassKey,
            (__bridge id)kSecAttrApplicationTag: applicationTag,
            (__bridge id)kSecAttrKeyType: (__bridge id)kSecAttrKeyTypeECSECPrimeRandom,
            (__bridge id)kSecReturnRef: @YES,
            (__bridge id)kSecUseAuthenticationContext: context
        };
        CFTypeRef found = NULL;
        OSStatus status = SecItemCopyMatching((__bridge CFDictionaryRef)query, &found);
        if (status == errSecItemNotFound) {
            CFErrorRef failure = NULL;
            SecAccessControlRef control = SecAccessControlCreateWithFlags(NULL,
                kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
                kSecAccessControlPrivateKeyUsage | kSecAccessControlBiometryCurrentSet, &failure);
            if (!control) {
                if (failure) CFRelease(failure);
                return response(@{@"error": @"无法创建批准钥匙的生物识别保护"});
            }
            NSDictionary *attributes = @{
                (__bridge id)kSecAttrKeyType: (__bridge id)kSecAttrKeyTypeECSECPrimeRandom,
                (__bridge id)kSecAttrKeySizeInBits: @256,
                (__bridge id)kSecAttrTokenID: (__bridge id)kSecAttrTokenIDSecureEnclave,
                (__bridge id)kSecPrivateKeyAttrs: @{
                    (__bridge id)kSecAttrIsPermanent: @YES,
                    (__bridge id)kSecAttrApplicationTag: applicationTag,
                    (__bridge id)kSecAttrAccessControl: (__bridge id)control
                }
            };
            SecKeyRef created = SecKeyCreateRandomKey((__bridge CFDictionaryRef)attributes, &failure);
            CFRelease(control);
            if (failure) CFRelease(failure);
            if (!created) return response(@{@"error": @"安全芯片无法创建批准钥匙，未改用软件钥匙"});
            CFRelease(created);
            // Re-query with this operation's fresh authentication context.
            status = SecItemCopyMatching((__bridge CFDictionaryRef)query, &found);
        }
        if (status != errSecSuccess || !found) {
            if (found) CFRelease(found);
            return response(@{@"error": @"批准钥匙不可用或生物识别已变化；没有替换原钥匙"});
        }
        SecKeyRef key = (SecKeyRef)found;
        SecKeyRef publicKey = SecKeyCopyPublicKey(key);
        CFErrorRef failure = NULL;
        NSData *publicData = publicKey ? CFBridgingRelease(SecKeyCopyExternalRepresentation(publicKey, &failure)) : nil;
        if (publicKey) CFRelease(publicKey);
        if (failure) { CFRelease(failure); failure = NULL; }
        if (!publicData || publicData.length != 65) {
            CFRelease(key);
            return response(@{@"error": @"无法读取批准钥匙公钥"});
        }
        NSMutableDictionary *result = [@{@"public_key": hex(publicData)} mutableCopy];
        if (message.length) {
            NSData *bytes = [message dataUsingEncoding:NSUTF8StringEncoding];
            NSData *signedData = CFBridgingRelease(SecKeyCreateSignature(key,
                kSecKeyAlgorithmECDSASignatureMessageX962SHA256, (__bridge CFDataRef)bytes, &failure));
            if (!signedData) {
                if (failure) CFRelease(failure);
                CFRelease(key);
                return response(@{@"error": @"Touch ID 未批准；请求保持待批"});
            }
            result[@"signature"] = hex(signedData);
        }
        CFRelease(key);
        [context invalidate];
        return response(result);
    }
}
